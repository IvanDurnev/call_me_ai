import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask
from app import whos
from app.durak import GameError
from app.game_routes import games_bp
from app.routes import main_bp


class WhosRulesTests(unittest.TestCase):
    def room(self, count=3):
        room = whos.new_room()
        room['host'] = 'host-secret'
        room['players'] = [dict(name=f'Игрок {i}', token=f'private-{i}', seen=0) for i in range(count)]
        return room

    def test_start_limits_and_unique_questions(self):
        for count in (2,13):
            with self.assertRaises(GameError): whos.start(self.room(count))
        for count in (3,12):
            room=self.room(count)
            whos.start(room)
            self.assertEqual(room['phase'],'voting')
            self.assertEqual(len(room['questions']),10)
            self.assertEqual(len(set(room['questions'])),10)
            self.assertEqual(room['round'],1)
            with self.assertRaises(GameError): whos.start(room)

    def test_secret_votes_and_self_vote(self):
        room=self.room()
        whos.start(room)
        whos.act(room,0,{'action':'vote','target':0})
        public=whos.view(room)
        self.assertIsNone(public['result'])
        self.assertEqual(public['totals'],[])
        self.assertEqual(public['history'],[])
        self.assertIsNone(public['own_vote'])
        self.assertFalse(public['has_voted'])
        for key in ('votes','host','token','questions'):
            self.assertNotIn(key,public)
        self.assertNotIn('private-',json.dumps(public))
        self.assertEqual(whos.view(room,0)['own_vote'],0)
        self.assertTrue(whos.view(room,0)['has_voted'])
        self.assertIsNone(whos.view(room,1)['own_vote'])
        with self.assertRaises(GameError): whos.act(room,0,{'action':'vote','target':1})

    def test_validation(self):
        room=self.room()
        whos.start(room)
        for target in (-1,3,True,1.0,'1',[],{}):
            with self.assertRaises(GameError): whos.act(room,0,{'action':'vote','target':target})
        with self.assertRaises(GameError): whos.act(room,0,{'action':'vote'})
        with self.assertRaises(GameError): whos.host_act(room,{'action':'reveal'})
        with self.assertRaises(GameError): whos.host_act(room,{'action':'next'})

    def test_results_ties_totals_and_no_double_count(self):
        room=self.room()
        whos.start(room)
        for i in range(3): whos.act(room,i,{'action':'vote','target':i})
        self.assertEqual(room['phase'],'results')
        result=whos.view(room)['result']
        self.assertEqual(result['counts'],[1,1,1])
        self.assertEqual(result['leaders'],[0,1,2])
        self.assertEqual(room['nominations'],[1,1,1])
        self.assertEqual(room['totals'],[1,1,1])
        with self.assertRaises(GameError): whos.host_act(room,{'action':'reveal'})
        with self.assertRaises(GameError): whos.act(room,0,{'action':'vote','target':0})
        self.assertEqual(len(room['history']),1)
        whos.host_act(room,{'action':'next'})
        self.assertEqual(room['round'],2)
        self.assertEqual(room['votes'],{})
        self.assertIsNone(whos.view(room)['result'])
        self.assertEqual(whos.view(room)['totals'],[])

    def test_abstentions_and_early_reveal(self):
        room=self.room()
        whos.start(room)
        whos.act(room,0,{'action':'vote','target':None})
        self.assertTrue(whos.view(room,0)['has_voted'])
        whos.act(room,1,{'action':'vote','target':2})
        whos.host_act(room,{'action':'reveal'})
        self.assertEqual(room['history'][0]['counts'],[0,0,1])
        self.assertEqual(room['history'][0]['skipped'],2)
        self.assertEqual(room['history'][0]['leaders'],[2])
        whos.host_act(room,{'action':'next'})
        for i in range(3): whos.act(room,i,{'action':'vote','target':None})
        self.assertEqual(room['history'][1]['leaders'],[])
        self.assertEqual(room['history'][1]['skipped'],3)
        self.assertEqual(room['nominations'],[0,0,1])

    def test_complete_party_and_rematch(self):
        room=self.room()
        whos.start(room)
        match=room['match']
        for number in range(1,11):
            self.assertEqual(room['round'],number)
            for i in range(3): whos.act(room,i,{'action':'vote','target':0})
            self.assertEqual(room['phase'],'results')
            whos.host_act(room,{'action':'next'})
        self.assertEqual(room['phase'],'finished')
        summary=whos.view(room)
        self.assertEqual(len(summary['history']),10)
        self.assertEqual(summary['totals'],[30,0,0])
        self.assertEqual(summary['nominations'],[10,0,0])
        whos.start(room)
        self.assertEqual(room['match'],match+1)
        self.assertEqual(room['totals'],[0,0,0])
        self.assertEqual(room['history'],[])


class WhosAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        self.app.config.update(TESTING=True,GAMES_DB_PATH=str(Path(self.temp.name)/'games.sqlite3'),GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp)
        self.app.register_blueprint(main_bp)
        self.client=self.app.test_client()
        created=self.client.post('/api/games/rooms',json={'game':'whos'})
        self.assertEqual(created.status_code,201)
        self.room=created.json
        self.base='/api/games/rooms/'+self.room['code']
        self.host={'Authorization':'Bearer '+self.room['token']}

    def tearDown(self): self.temp.cleanup()

    def join(self,name):
        response=self.client.post(self.base+'/join',json={'name':name})
        self.assertEqual(response.status_code,200)
        return {'Authorization':'Bearer '+response.json['token']}

    def state(self,headers=None): return self.client.get(self.base,headers=headers or self.host).json

    def action(self,headers,action,**extra):
        state=self.state(headers)
        return self.client.post(self.base+'/action',headers=headers,json=dict(action=action,revision=state['revision'],phase=state['phase'],match=state['match'],round=state['round'],**extra))

    def test_creation_routes_and_qr(self):
        self.assertIn('/games/whos/',self.room['url'])
        self.assertEqual(self.client.get(self.room['url']).status_code,200)
        self.assertIn(b'whos.js',self.client.get('/games/join/'+self.room['code']).data)
        self.assertEqual(self.client.get('/games/join?code='+self.room['code'].lower()).status_code,302)
        qr=self.client.get(self.base+'/qr.svg')
        self.assertEqual(qr.status_code,200)
        self.assertIn(b'<svg',qr.data)
        self.assertEqual(self.client.get(self.base).status_code,400)
        self.assertEqual(self.client.post('/api/games/rooms',json={'game':[]}).status_code,400)
        self.assertIn('Кто из нас?',self.client.get('/games').text)

    def test_capacity_permissions_and_removed_credentials(self):
        players=[self.join(str(i)) for i in range(12)]
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'extra'}).status_code,400)
        self.assertEqual(self.action(players[0],'start').status_code,400)
        self.assertEqual(self.action(self.host,'remove',target=0).status_code,200)
        self.assertEqual(self.client.get(self.base,headers=players[0]).status_code,400)
        self.assertEqual(self.action(self.host,'start').status_code,200)
        self.assertEqual(self.action(players[1],'reveal').status_code,400)
        self.assertEqual(self.action(self.host,'vote',target=1).status_code,400)
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'late'}).status_code,400)

    def test_concurrent_votes_accept_same_revision_and_hide_choices(self):
        players=[self.join(str(i)) for i in range(12)]
        self.action(self.host,'start')
        state=self.state()
        common=dict(action='vote',target=2,revision=state['revision'],phase=state['phase'],match=state['match'],round=state['round'])
        def cast(headers):
            with self.app.test_client() as client:
                return client.post(self.base+'/action',headers=headers,json=common).status_code
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses=list(pool.map(cast,players))
        self.assertEqual(statuses,[200]*12)
        public=self.state()
        self.assertEqual(public['phase'],'results')
        self.assertEqual(public['result']['counts'][2],12)
        self.assertIsNone(public['own_vote'])
        self.assertNotIn('votes',public)
        self.assertEqual(self.state(players[0])['own_vote'],2)
        self.assertEqual(public['nominations'][2],1)

    def test_old_vote_cannot_enter_next_question_or_restarted_party(self):
        players=[self.join(str(i)) for i in range(3)]
        self.action(self.host,'start')
        state=self.state()
        stale=dict(action='vote',target=1,revision=state['revision'],phase=state['phase'],match=state['match'],round=state['round'])
        self.action(players[0],'vote',target=1)
        self.action(self.host,'reveal')
        self.action(self.host,'next')
        self.assertEqual(self.client.post(self.base+'/action',headers=players[1],json=stale).status_code,409)
        self.action(self.host,'reset')
        self.assertEqual(self.state(players[0])['phase'],'lobby')
        self.action(self.host,'start')
        self.assertEqual(self.client.post(self.base+'/action',headers=players[1],json=stale).status_code,409)
        self.assertEqual(self.state()['vote_count'],0)

    def test_complete_party_over_api_and_reconnect(self):
        players=[self.join(str(i)) for i in range(3)]
        self.action(self.host,'start')
        for number in range(1,11):
            for headers in players: self.assertEqual(self.action(headers,'vote',target=0).status_code,200)
            self.assertEqual(self.action(self.host,'next').status_code,200)
        summary=self.state()
        self.assertEqual(summary['phase'],'finished')
        self.assertEqual(len(summary['history']),10)
        other=Flask('restarted')
        other.config.update(TESTING=True,GAMES_DB_PATH=self.app.config['GAMES_DB_PATH'])
        other.register_blueprint(games_bp)
        restored=other.test_client().get(self.base,headers=players[0]).json
        self.assertEqual(restored['totals'],[30,0,0])
        self.assertTrue(restored['has_voted'])
        self.assertEqual(self.action(self.host,'start').status_code,200)
        self.assertFalse(self.state(players[0])['has_voted'])
