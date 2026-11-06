import copy
import json
import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from app import activity as a
from app.activity_data import CARDS, validate_cards
from app.durak import GameError
from app.game_routes import games_bp, database, load_room, save_room
from app.routes import main_bp


def room(count=2):
    r=a.new_room();r['players']=[dict(name=f'Команда {i}') for i in range(count)]
    a.start(r,rng=random.Random(8));return r


def prepare(r,difficulty=3,category='drawA'):
    p=r['active']['presenter']
    a.act(r,p,dict(action='take_turn'))
    a.act(r,p,dict(action='difficulty',difficulty=difficulty),rng=random.Random(4))
    if r['phase']=='choose_task':
        options=a.view(r,p)['card_options']
        a.act(r,p,dict(action='task',category=category if category in options else next(iter(options))))
    return p


def guessed(r,now=120):
    a.act(r,r['active']['presenter'],dict(action='guessed',match=r['match'],turn_id=r['active']['id']),now=now)


class ActivityRulesTests(unittest.TestCase):
    def test_short_complete_original_decks(self):
        self.assertEqual(len(CARDS),90)
        texts=[t['text'].casefold().replace('ё','е') for c in CARDS for t in c['tasks'].values()]
        self.assertEqual(len(set(texts)),540)
        self.assertTrue(all(1<=len(t.split())<=2 for t in texts))
        self.assertTrue(all(not t['isOpenRound'] for c in CARDS for t in c['tasks'].values()))
        broken=copy.deepcopy(CARDS);broken[0]['tasks']['drawA']['text']='Три разных слова'
        with self.assertRaises(ValueError):validate_cards(broken)

    def test_team_phones_and_minimum_four_humans(self):
        for n in (2,3,4):
            r=room(n);self.assertEqual(len(r['positions']),n)
            self.assertEqual(sum(r['settings']['sizes']),2*n)
            self.assertEqual([t['members'] for t in r['settings']['teams']],[[i] for i in range(n)])
        for n in (0,1,5):
            r=a.new_room();r['players']=[dict(name=str(i)) for i in range(n)]
            with self.assertRaises(GameError):a.start(r)
        for settings in [dict(mode='individual'),dict(sizes=[1,2]),dict(sizes=[2]),dict(sizes=[True,2]),dict(starting_side=2)]:
            with self.assertRaises(GameError):a.validate_settings(settings,room()['players'])

    def test_sizes_and_starting_team(self):
        r=a.new_room();r['players']=[dict(name='Синие'),dict(name='Красные')]
        a.host_act(r,dict(action='launch',settings=dict(sizes=[5,3],starting_side=1,precision='Точный смысл')))
        self.assertEqual(r['cursor'],1);self.assertEqual(r['active']['presenter'],1)
        self.assertEqual(r['settings']['sizes'],[5,3])

    def test_word_visible_only_on_active_team_phone_while_performing(self):
        r=room();p=prepare(r);word=a.view(r,p)['secret_task']['text']
        a.act(r,p,dict(action='perform'),now=100)
        self.assertEqual(a.view(r,p)['secret_task']['text'],word)
        for viewer in (None,1):
            public=a.view(r,viewer)
            self.assertIsNone(public['secret_task']);self.assertIsNone(public['card_options'])
            self.assertIsNone(public['opponent_card']);self.assertNotIn(word,json.dumps(public,ensure_ascii=False))

    def test_guessed_applies_exact_difficulty_once_without_host_review(self):
        for difficulty in (3,4,5):
            r=room();p=prepare(r,difficulty);a.act(r,p,dict(action='perform'),now=100)
            guessed(r,159.99)
            self.assertEqual(r['phase'],'board');self.assertEqual(r['positions'],[difficulty,0])
            self.assertEqual(len(r['history']),1)
            with self.assertRaises(GameError):guessed(r,159.999)
            self.assertEqual(r['positions'],[difficulty,0])

    def test_deadline_is_exclusive_and_auto_failure(self):
        for now in (160,161,10000):
            r=room();p=prepare(r);a.act(r,p,dict(action='perform'),now=100)
            with self.assertRaises(GameError):guessed(r,now)
            self.assertEqual(r['phase'],'board');self.assertEqual(r['positions'],[0,0])
            self.assertTrue(r['active']['expired']);self.assertEqual(r['history'][0]['outcome'],'failure')
            self.assertFalse(a.expire(r,now+1));self.assertEqual(len(r['history']),1)

    def test_unauthorised_or_stale_success_is_rejected(self):
        r=room();p=prepare(r)
        with self.assertRaises(GameError):guessed(r,101)
        a.act(r,p,dict(action='perform'),now=100)
        data=dict(action='guessed',match=r['match'],turn_id=r['active']['id'])
        with self.assertRaises(GameError):a.act(r,1,data,now=101)
        for extra in [dict(match=0),dict(turn_id='old')]:
            with self.assertRaises(GameError):a.act(r,0,dict(data,**extra),now=101)
        self.assertEqual(r['positions'],[0,0])

    def test_no_individual_open_or_manual_judging_actions(self):
        r=room();p=prepare(r)
        self.assertFalse(r['active']['open'])
        for action in ('memorize','opponents','ready','stop'):
            with self.assertRaises(GameError):a.act(r,p,dict(action=action))
        for action in ('result','apply','perform','stop'):
            with self.assertRaises(GameError):a.host_act(r,dict(action=action))

    def test_failed_turn_rotates_teams_and_phone_is_reused(self):
        r=room();p=prepare(r);a.act(r,p,dict(action='give_up'))
        self.assertEqual(r['positions'],[0,0])
        a.host_act(r,dict(action='next'));self.assertEqual(r['active']['presenter'],1)
        p=prepare(r);a.act(r,p,dict(action='give_up'));a.host_act(r,dict(action='next'))
        self.assertEqual(r['active']['presenter'],0)
        p=prepare(r,4,'mimeB');self.assertEqual(r['active']['category'],'mimeB')

    def test_cell_controls_type_and_offers_both_matching_tasks(self):
        for position,expected in [(0,{'drawA','drawB','explainA','explainB','mimeA','mimeB'}),(1,{'drawA','drawB'}),(2,{'explainA','explainB'}),(3,{'mimeA','mimeB'}),(4,{'drawA','drawB'}),(5,{'explainA','explainB'}),(6,{'mimeA','mimeB'})]:
            r=room();r['positions'][0]=position
            a.act(r,0,dict(action='take_turn'));a.act(r,0,dict(action='difficulty',difficulty=3))
            self.assertEqual(r['phase'],'choose_task')
            self.assertEqual(set(a.view(r,0)['card_options']),expected)
            self.assertIsNone(a.view(r)['card_options']);self.assertIsNone(a.view(r,1)['card_options'])
            if position:
                forbidden=next(k for k in ['drawA','explainA','mimeA'] if k not in expected)
                with self.assertRaises(GameError):a.act(r,0,dict(action='task',category=forbidden))
                self.assertEqual(r['phase'],'choose_task')
            choice=sorted(expected)[-1]
            a.act(r,0,dict(action='task',category=choice))
            self.assertEqual(r['active']['category'],choice)
            with self.assertRaises(GameError):a.act(r,0,dict(action='difficulty',difficulty=5))

    def test_precision_setting_removed_and_old_saved_rooms_still_load(self):
        r=room();self.assertNotIn('precision',r['settings'])
        r['settings']['precision']='Legacy setting'
        a.validate(r)
        self.assertNotIn('precision',a.view(r)['settings'])


    def test_teams_share_cells_without_moving_others_and_finish_clamps(self):
        t=a.build_move_transaction([0,3,3,2],{0:3},'id')
        self.assertEqual(t['after'],[3,3,3,2]);self.assertEqual(t['bumped'],[])
        t=a.build_move_transaction([47,49],{0:5},'id')
        self.assertEqual(t['after'],[49,49]);self.assertEqual(t['raw_destinations'][0],52)
        r=room();r['positions'][0]=47;p=prepare(r,5);a.act(r,p,dict(action='perform'),now=100);guessed(r)
        self.assertEqual(r['phase'],'finished');self.assertEqual(r['winners'],[0])

    def test_success_only_moves_active_team_onto_an_occupied_cell(self):
        r=room(4);r['positions']=[0,5,5,2]
        p=prepare(r,5);a.act(r,p,dict(action='perform'),now=100);guessed(r)
        self.assertEqual(r['positions'],[5,5,5,2])
        self.assertEqual(r['history'][-1]['transaction']['bumped'],[])

    def test_undo_restores_card_deck_queue_and_discards_next_attempt(self):
        r=room();before=copy.deepcopy(r['decks']);p=prepare(r);card=r['active']['card']
        a.act(r,p,dict(action='perform'),now=100);guessed(r)
        a.host_act(r,dict(action='next'));prepare(r,4)
        a.host_act(r,dict(action='undo'))
        self.assertEqual(r['positions'],[0,0]);self.assertEqual(r['decks'],before)
        self.assertEqual(r['active']['presenter'],0);self.assertEqual(r['history'],[])
        prepare(r);self.assertEqual(r['active']['card'],card)

    def test_exhausted_deck_and_undo(self):
        r=room();deck=r['decks']['3'];deck['discard']=deck['draw'];deck['draw']=[]
        r['checkpoint']=a.snapshot(r);before=copy.deepcopy(r['decks']);p=prepare(r);a.act(r,p,dict(action='give_up'))
        a.host_act(r,dict(action='undo'));self.assertEqual(r['decks'],before);a.validate(r)

    def test_drawing_authorisation_idempotency_and_timer(self):
        r=room();p=prepare(r);a.act(r,p,dict(action='perform'),now=100)
        data=dict(action='draw',kind='stroke',match=r['match'],turn_id=r['active']['id'],op_id='x0',stroke_id='s1',seq=0,tool='pen',width=7,points=[[.1,.2],[.4,.5]])
        a.act(r,p,data,now=101);a.act(r,p,data,now=101)
        self.assertEqual(len(r['drawing']['strokes'][0]['points']),2)
        with self.assertRaises(GameError):a.act(r,1,data,now=101)
        with self.assertRaises(GameError):a.act(r,p,dict(data,op_id='nan',stroke_id='nan',points=[[float('nan'),.2]]),now=101)
        a.act(r,p,dict(data,kind='undo',op_id='undo'),now=101)
        self.assertEqual(r['drawing']['strokes'],[])
        with self.assertRaises(GameError):a.act(r,p,data,now=160)
        self.assertEqual(r['positions'],[0,0]);self.assertEqual(len(r['history']),1)

    def test_restore_running_deadline_and_timeout(self):
        r=room();p=prepare(r);a.act(r,p,dict(action='perform'),now=100)
        restored=json.loads(json.dumps(r));a.validate(restored)
        self.assertEqual(a.view(restored,p)['deadline'],160)
        self.assertFalse(a.expire(restored,159));self.assertTrue(a.expire(restored,160))
        self.assertEqual(restored['positions'],[0,0])

    def test_corrupt_or_old_save_is_not_silently_reset(self):
        for key,value in [('positions',[-1,0]),('schemaVersion',1),('ruleset','show-explain-family-v1')]:
            r=room();r[key]=value;before=copy.deepcopy(r)
            with self.assertRaisesRegex(GameError,'несовместимо'):a.validate(r)
            self.assertEqual(r,before)


class ActivityAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        self.app.config.update(TESTING=True,GAMES_DB_PATH=str(Path(self.temp.name)/'games.sqlite3'),GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp);self.app.register_blueprint(main_bp);self.client=self.app.test_client()
        self.room=self.client.post('/api/games/rooms',json={'game':'activity'}).json
        self.base='/api/games/rooms/'+self.room['code'];self.host={'Authorization':'Bearer '+self.room['token']}
        self.players=[]
        for i in range(2):self.players.append({'Authorization':'Bearer '+self.client.post(self.base+'/join',json={'name':f'Команда {i}'}).json['token']})
    def tearDown(self):self.temp.cleanup()
    def state(self,headers=None):return self.client.get(self.base,headers=headers or self.host).json
    def action(self,name,headers=None,**extra):
        state=self.state(headers)
        return self.client.post(self.base+'/action',headers=headers or self.host,json=dict(action=name,revision=state['revision'],match=state['match'],turn_id=state['turn_id'],**extra))
    def start(self):self.assertEqual(self.action('launch',settings=dict(sizes=[3,2])).status_code,200)
    def perform(self):
        p=self.state()['presenter'];headers=self.players[p]
        for name,extra in [('take_turn',{}),('difficulty',dict(difficulty=3))]:self.assertEqual(self.action(name,headers,**extra).status_code,200)
        if self.state()['phase']=='choose_task':self.assertEqual(self.action('task',headers,category='drawA').status_code,200)
        self.assertEqual(self.action('perform',headers).status_code,200)
        return headers

    def test_join_routes_and_four_team_phone_limit(self):
        self.assertEqual(self.client.get(self.room['url']).status_code,200)
        self.assertIn('Название команды',self.client.get('/games/join/'+self.room['code']).text)
        self.assertEqual(self.client.get(self.base+'/qr.svg').status_code,200)
        for name in ('Третья','Четвёртая'):self.assertEqual(self.client.post(self.base+'/join',json={'name':name}).status_code,200)
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'Пятая'}).status_code,400)
        self.assertEqual(self.action('launch',self.players[0],settings={}).status_code,400)

    def test_private_card_and_phone_success_no_host_judgement(self):
        self.start();headers=self.perform();word=self.state(headers)['secret_task']['text']
        self.assertIsNone(self.state()['secret_task']);self.assertIsNone(self.state(self.players[1])['secret_task'])
        self.assertNotIn(word,json.dumps(self.state(),ensure_ascii=False))
        self.assertEqual(self.action('guessed',self.players[1]).status_code,400)
        self.assertEqual(self.action('guessed',headers).status_code,200)
        self.assertEqual(self.state()['positions'],[3,0]);self.assertEqual(self.state()['phase'],'board')

    def test_second_team_lands_on_first_without_pushing_it_back(self):
        self.start();headers=self.perform();self.action('guessed',headers)
        self.assertEqual(self.state()['positions'],[3,0])
        self.action('next');headers=self.perform();self.action('guessed',headers)
        state=self.state()
        self.assertEqual(state['positions'],[3,3])
        self.assertEqual(state['last_transaction']['bumped'],[])
        self.assertEqual(state['last_transaction']['before'],[3,0])
        self.action('undo');self.assertEqual(self.state()['positions'],[3,0])

    def test_non_start_phone_only_gets_tasks_matching_cell_method(self):
        self.start();headers=self.perform();self.action('guessed',headers)
        self.action('next');headers=self.perform();self.action('give_up',headers);self.action('next')
        headers=self.players[0]
        self.action('take_turn',headers);self.action('difficulty',headers,difficulty=3)
        private=self.state(headers)
        self.assertEqual(private['phase'],'choose_task')
        self.assertEqual(set(private['card_options']),{'mimeA','mimeB'})
        self.assertIsNone(self.state()['card_options'])
        self.assertIsNone(self.state(self.players[1])['card_options'])
        self.assertEqual(self.action('task',headers,category='drawA').status_code,400)
        self.assertEqual(self.action('task',headers,category='mimeB').status_code,200)
        self.assertEqual(self.state(headers)['category'],'mimeB')
        self.assertNotIn('precision',self.state()['settings'])

    def test_concurrent_success_is_applied_once(self):
        self.start();headers=self.perform();state=self.state(headers)
        body=dict(action='guessed',revision=state['revision'],match=state['match'],turn_id=state['turn_id'])
        def apply(_):
            with self.app.test_client() as client:return client.post(self.base+'/action',headers=headers,json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(apply,range(2)))
        self.assertEqual(sorted(statuses),[200,409]);self.assertEqual(self.state()['positions'],[3,0])
        self.assertEqual(len(self.state()['history']),1)

    def test_timeout_saved_even_when_late_button_is_rejected(self):
        self.start();headers=self.perform();state=self.state(headers)
        body=dict(action='guessed',revision=state['revision'],match=state['match'],turn_id=state['turn_id'])
        with self.app.app_context(),database() as db:
            r=load_room(db,self.room['code']);r['deadline']=1;save_room(db,self.room['code'],r)
        response=self.client.post(self.base+'/action',headers=headers,json=body)
        self.assertEqual(response.status_code,409)
        state=self.state();self.assertEqual(state['positions'],[0,0]);self.assertTrue(state['expired'])
        self.assertEqual(len(state['history']),1);self.assertEqual(state['phase'],'board')

    def test_late_drawing_saves_timeout_before_rejecting_stroke(self):
        self.start();headers=self.perform();state=self.state(headers)
        body=dict(action='draw',revision=state['revision'],match=state['match'],turn_id=state['turn_id'],op_id='late',kind='stroke',stroke_id='late',seq=0,tool='pen',width=7,points=[[.1,.1]])
        with self.app.app_context(),database() as db:
            r=load_room(db,self.room['code']);r['deadline']=1;save_room(db,self.room['code'],r)
        self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=body).status_code,409)
        state=self.state();self.assertEqual(state['phase'],'board');self.assertTrue(state['expired'])
        self.assertEqual(len(state['history']),1);self.assertEqual(state['drawing']['strokes'],[])

    def test_drawing_revision_does_not_block_guessed_and_undo_restores(self):
        self.start();headers=self.perform();state=self.state(headers)
        body=dict(action='guessed',revision=state['revision'],match=state['match'],turn_id=state['turn_id'])
        stroke=dict(body,action='draw',op_id='one',kind='stroke',stroke_id='one',seq=0,tool='pen',width=7,points=[[.1,.1]])
        for _ in range(2):self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=stroke).status_code,200)
        self.assertEqual(len(self.state()['drawing']['strokes'][0]['points']),1)
        self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=body).status_code,200)
        self.action('next');self.assertEqual(self.state()['presenter'],1)
        self.assertEqual(self.action('undo').status_code,200)
        self.assertEqual(self.state()['presenter'],0);self.assertEqual(self.state()['positions'],[0,0])
        self.assertNotEqual(self.state()['turn_id'],body['turn_id'])
        self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=body).status_code,409)

    def test_full_circle_timeout_next_team_then_success_and_restart(self):
        self.start();self.perform()
        with self.app.app_context(),database() as db:
            r=load_room(db,self.room['code']);r['deadline']=1;save_room(db,self.room['code'],r)
        self.assertEqual(self.state()['phase'],'board');self.action('next')
        headers=self.perform();self.assertEqual(self.action('guessed',headers).status_code,200)
        self.assertEqual(self.state()['positions'],[0,3]);self.action('next')
        self.assertEqual(self.state()['presenter'],0)
        other=Flask('restored');other.config.update(TESTING=True,GAMES_DB_PATH=self.app.config['GAMES_DB_PATH']);other.register_blueprint(games_bp)
        restored=other.test_client().get(self.base,headers=self.host).json
        self.assertEqual(restored['positions'],[0,3]);self.assertEqual(len(restored['history']),2)
