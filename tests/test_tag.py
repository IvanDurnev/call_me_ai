import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from simple_websocket import ConnectionClosed

from app import tag
from app.durak import GameError
from app.game_routes import games_bp, database, load_room, save_room
from app.routes import main_bp
from app.tag_socket import decode, serve


class TagPhysicsTests(unittest.TestCase):
    def room(self,count=2):
        room=tag.new_room()
        room['host']='host-private'
        for i in range(count):
            player=dict(name=f'P{i}',token=f'token-{i}',seen=100)
            room['players'].append(player)
            tag.on_join(room,player)
        with patch('app.tag.time.time',return_value=100): tag.start(room)
        room['host_seen']=100
        room['tagger']=0
        for player in room['players']: player['live_seen']=100
        return room

    def test_capacity_colors_and_spawn_separation(self):
        room=self.room(8)
        self.assertEqual(len({p['color'] for p in room['players']}),8)
        for i,pos in enumerate(room['positions']):
            for other in room['positions'][i+1:]:
                self.assertGreater(math.hypot(other['x']-pos['x'],other['y']-pos['y']),2*tag.RADIUS)
        with self.assertRaises(GameError): tag.start(room)
        room=tag.new_room();room['players']=[dict(name='Only')]
        with self.assertRaises(GameError): tag.start(room)

    def test_vectors_are_finite_and_cannot_increase_speed(self):
        for x,y in [(True,0),(None,1),('1',0),(float('nan'),0),(0,float('inf')),(10**400,0)]:
            with self.assertRaises(GameError): tag.vector(x,y)
        x,y=tag.vector(100,100)
        self.assertAlmostEqual(math.hypot(x,y),1)
        room=self.room();room['countdown']=0
        before=dict(room['positions'][0])
        tag.set_input(room,0,dict(match=room['match'],x=100,y=100),100)
        tag.advance(room,100.05)
        after=room['positions'][0]
        self.assertAlmostEqual(math.hypot(after['x']-before['x'],after['y']-before['y']),tag.SPEED*1.07*.05)

    def test_deadman_switch_and_arena_bounds(self):
        room=self.room();room['countdown']=0
        room['positions'][0].update(x=1190,y=700)
        tag.set_input(room,0,dict(match=room['match'],x=1,y=1),100)
        tag.advance(room,100.05)
        self.assertLessEqual(room['positions'][0]['x'],tag.WIDTH-tag.RADIUS)
        self.assertLessEqual(room['positions'][0]['y'],tag.HEIGHT-tag.RADIUS)
        before=dict(room['positions'][0])
        tag.advance(room,100.5)
        self.assertEqual(before,room['positions'][0])

    def test_countdown_and_pause_do_not_consume_round_time(self):
        room=self.room()
        tag.advance(room,100.05)
        self.assertLess(room['countdown'],3)
        self.assertEqual(room['elapsed'],0)
        self.assertFalse(room['paused'])
        tag.advance(room,103)
        self.assertTrue(room['paused'])
        self.assertEqual(room['elapsed'],0)
        self.assertIn('Ждём',room['pause_reason'])
        room['host_seen']=103
        for player in room['players']:player['live_seen']=103
        tag.advance(room,103.05)
        self.assertFalse(room['paused'])

    def test_contact_passes_tag_and_prevents_immediate_return(self):
        room=self.room();room['countdown']=0
        room['positions']=[dict(x=500,y=300),dict(x=520,y=300)]
        tag.advance(room,100.05)
        self.assertEqual(room['tagger'],1)
        self.assertEqual(room['catches'],[1,0])
        self.assertEqual(room['tag_version'],1)
        tag.advance(room,100.1)
        self.assertEqual(room['tagger'],1)
        room['elapsed']=1.4
        tag.advance(room,100.15)
        self.assertEqual(room['tagger'],1)
        room['elapsed']=2.4
        tag.advance(room,100.2)
        self.assertEqual(room['tagger'],0)
        self.assertEqual(room['catches'],[1,1])

    def test_disconnected_players_cannot_move_or_run_the_clock(self):
        room=self.room();room['countdown']=0
        tag.set_input(room,0,dict(match=room['match'],x=1,y=0),100)
        before=dict(room['positions'][0])
        room['players'][1]['live_seen']=0
        tag.advance(room,100.05)
        self.assertTrue(room['paused'])
        self.assertEqual(room['positions'][0],before)
        self.assertEqual(room['elapsed'],0)

    def test_worker_delay_cannot_teleport_characters(self):
        room=self.room();room['countdown']=0
        room['host_seen']=1000
        for player in room['players']:player['live_seen']=1000
        tag.set_input(room,0,dict(match=room['match'],x=1,y=0),1000)
        x=room['positions'][0]['x']
        tag.advance(room,1000)
        self.assertLessEqual(room['positions'][0]['x']-x,tag.SPEED*1.07*.1+1e-8)
        self.assertLessEqual(room['elapsed'],.1)

    def test_duration_scores_and_rematch(self):
        room=self.room();room['countdown']=0
        now=100
        for _ in range(2402):
            now+=.05
            room['host_seen']=now
            for player in room['players']:player['live_seen']=now
            tag.advance(room,now)
        self.assertEqual(room['phase'],'finished')
        self.assertAlmostEqual(sum(room['tag_seconds']),tag.DURATION)
        self.assertEqual(room['winners'],[1])
        old_match=room['match']
        tag.start(room)
        self.assertEqual(room['match'],old_match+1)
        self.assertEqual(room['catches'],[0,0])
        self.assertEqual(room['tag_seconds'],[0,0])
        with self.assertRaises(GameError): tag.set_input(room,0,dict(match=old_match,x=1,y=0),now)

    def test_private_state_and_forged_positions(self):
        room=self.room()
        tag.set_input(room,0,dict(match=room['match'],x=0,y=0,position={'x':99999}),100)
        with patch('app.tag.time.time',return_value=100): state=tag.view(room,0)
        for key in ('host','controls','host_socket','host_seen'):
            self.assertNotIn(key,state)
        self.assertNotIn('token-',json.dumps(state))
        self.assertNotIn('socket_id',json.dumps(state))
        self.assertEqual(state['you'],0)
        self.assertNotEqual(state['players'][0]['x'],99999)


class FakeSocket:
    def __init__(self,messages):self.messages=list(messages);self.sent=[];self.closed=False
    def receive(self,timeout=None):
        if not self.messages:raise ConnectionClosed()
        return self.messages.pop(0)
    def send(self,data):self.sent.append(json.loads(data))
    def close(self):self.closed=True


class TagAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        self.app.config.update(TESTING=True,GAMES_DB_PATH=str(Path(self.temp.name)/'games.sqlite3'),GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp);self.app.register_blueprint(main_bp);self.client=self.app.test_client()
        self.created=self.client.post('/api/games/rooms',json={'game':'tag'}).json
        self.code=self.created['code'];self.base='/api/games/rooms/'+self.code
        self.host={'Authorization':'Bearer '+self.created['token']}
    def tearDown(self):self.temp.cleanup()
    def join(self,name):
        response=self.client.post(self.base+'/join',json={'name':name});self.assertEqual(response.status_code,200)
        return response.json['token']
    def state(self,token=None):return self.client.get(self.base,headers={'Authorization':'Bearer '+token} if token else self.host).json
    def action(self,action,token=None,**extra):
        state=self.state(token)
        return self.client.post(self.base+'/action',headers={'Authorization':'Bearer '+token} if token else self.host,json=dict(action=action,revision=state['revision'],**extra))

    def test_routes_colors_capacity_and_revocation(self):
        self.assertIn('/games/tag/',self.created['url'])
        self.assertEqual(self.client.get(self.created['url']).status_code,200)
        self.assertIn(b'tag.js',self.client.get('/games/join/'+self.code).data)
        self.assertEqual(self.client.get(self.base+'/qr.svg').status_code,200)
        players=[self.join(str(i)) for i in range(8)]
        self.assertEqual(len({p['color'] for p in self.state()['players']}),8)
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'extra'}).status_code,400)
        self.assertEqual(self.action('remove',target=0).status_code,200)
        self.join('New player')
        self.assertEqual(len({p['color'] for p in self.state()['players']}),8)
        self.assertEqual(self.client.get(self.base,headers={'Authorization':'Bearer '+players[0]}).status_code,400)

    def test_host_controls_and_input_cannot_use_http(self):
        a,b=self.join('A'),self.join('B')
        self.assertEqual(self.action('start',token=a).status_code,400)
        self.assertEqual(self.action('start').status_code,200)
        self.assertEqual(self.action('move',token=a,x=999,y=999).status_code,400)
        self.assertEqual(self.action('stop',token=a).status_code,400)
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'late'}).status_code,400)
        self.assertEqual(self.action('stop').status_code,200)
        self.assertEqual(self.state()['phase'],'finished')
        self.assertEqual(self.action('reset').status_code,200)
        self.assertEqual(self.state(a)['phase'],'lobby')
        self.assertEqual(len(self.state()['players']),2)

    def test_round_progression_persists_objects_and_lobby_resets_level(self):
        a,b=self.join('A'),self.join('B')
        for number in range(1,7):
            self.assertEqual(self.action('start').status_code,200)
            state=self.state(a)
            self.assertEqual(state['round'],number)
            self.assertEqual(len(state['arena']['walls']),min(8,(number-1)*2))
            self.assertEqual(len(state['arena']['portals']),2 if number>=3 else 0)
            self.assertIn('shield_seconds',state['players'][0])
            with self.app.app_context(),database() as connection:
                persisted=load_room(connection,self.code)
                self.assertEqual(persisted['arena'],state['arena'])
                self.assertEqual(persisted['round'],number)
            self.assertEqual(self.action('stop').status_code,200)
        old_match=self.state()['match']
        self.assertEqual(self.action('reset').status_code,200)
        self.assertEqual(self.action('start').status_code,200)
        state=self.state()
        self.assertEqual(state['round'],1)
        self.assertGreater(state['match'],old_match)
        self.assertEqual(state['arena']['walls'],[])

    def test_socket_auth_frames_and_disconnect_stop(self):
        a,b=self.join('A'),self.join('B');self.action('start')
        state=self.state()
        ws=FakeSocket([json.dumps(dict(type='auth',token=a,match=state['match'])),json.dumps(dict(type='input',match=state['match'],x=1,y=0))])
        with self.app.test_request_context('/ws/games/tag/'+self.code):serve(ws,self.code)
        self.assertTrue(ws.closed)
        self.assertTrue(any(frame['type']=='state' for frame in ws.sent))
        self.assertNotIn('token',json.dumps(ws.sent))
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.code)
            self.assertEqual(room['controls'][0]['x'],0)
            self.assertEqual(room['players'][0]['live_seen'],0)
        rejected=FakeSocket([json.dumps(dict(type='auth',token='wrong',match=state['match']))])
        with self.app.test_request_context('/ws/games/tag/'+self.code):serve(rejected,self.code)
        self.assertEqual(rejected.sent[0]['type'],'error')

    def test_new_controller_replaces_old_without_clearing_new_input(self):
        a,b=self.join('A'),self.join('B');self.action('start')
        match=self.state()['match']
        app,code=self.app,self.code
        class ReplacedSocket(FakeSocket):
            def receive(self,timeout=None):
                if len(self.messages)==1:
                    with app.app_context(),database() as connection:
                        room=load_room(connection,code)
                        room['players'][0].update(socket_id='new-controller',live_seen=9999999999)
                        room['controls'][0].update(x=.5,y=0)
                        save_room(connection,code,room)
                return super().receive(timeout)
        ws=ReplacedSocket([json.dumps(dict(type='auth',token=a,match=match)),json.dumps(dict(type='ping'))])
        with self.app.test_request_context('/ws/games/tag/'+self.code):serve(ws,self.code)
        self.assertEqual(ws.sent[-1]['type'],'error')
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.code)
            self.assertEqual(room['players'][0]['socket_id'],'new-controller')
            self.assertEqual(room['controls'][0]['x'],.5)
            self.assertEqual(room['players'][0]['live_seen'],9999999999)

    def test_invalid_socket_frames_and_old_match(self):
        for value in ('[]','null','{','x'*1025,b'{}'):
            with self.assertRaises(GameError):decode(value)
        a,b=self.join('A'),self.join('B');self.action('start')
        rejected=FakeSocket([json.dumps(dict(type='auth',token=a,match=0))])
        with self.app.test_request_context('/ws/games/tag/'+self.code):serve(rejected,self.code)
        self.assertEqual(rejected.sent[0]['type'],'error')
        self.assertEqual(self.client.get(self.base).status_code,400)
