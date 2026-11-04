import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
from app import activity as a
from app.activity_data import CARDS, CARD_BY_ID, CATEGORIES, validate_cards
from app.durak import GameError
from app.game_routes import games_bp, database, load_room, save_room
from app.routes import main_bp


def make_room(mode='teams',count=4):
    room=a.new_room()
    room['players']=[dict(name=f'P{i}',token=f'private-{i}',seen=0) for i in range(count)]
    room['host']='host-secret'
    settings=dict(mode=mode,precision='Достаточно точного смысла',starting_side=0,
                  teams=[dict(name='Синие',members=[0,1],first_presenter=0),dict(name='Кораллы',members=[2,3],first_presenter=2)])
    room['settings']=a.validate_settings(settings,room['players'])
    a.start(room,rng=random.Random(5),now=100)
    return room


def prepare(room,difficulty=3,category=None):
    presenter=room['active']['presenter']
    a.act(room,presenter,dict(action='take_turn'),now=100)
    a.act(room,presenter,dict(action='difficulty',difficulty=difficulty),now=100)
    if room['phase']=='choose_task':
        category=category or 'drawA'
        a.act(room,presenter,dict(action='task',category=category),now=100)
    return presenter


def apply(room,outcome='success',guesser=1):
    room['phase']='review'
    a.host_act(room,dict(action='result',outcome=outcome,guesser=guesser),now=100)
    a.host_act(room,dict(action='apply'),now=100)


class ActivityRulesTests(unittest.TestCase):
    def test_editorial_decks_complete_unique_and_open_flags(self):
        validate_cards(CARDS)
        self.assertEqual(len(CARDS),90)
        self.assertEqual(sum(t['isOpenRound'] for c in CARDS for t in c['tasks'].values()),108)
        self.assertEqual(len({t['text'].casefold() for c in CARDS for t in c['tasks'].values()}),540)
        damaged=copy.deepcopy(CARDS);del damaged[0]['tasks']['drawA']
        with self.assertRaises(ValueError):validate_cards(damaged)

    def test_setup_limits_membership_and_first_presenter(self):
        room=make_room();settings=copy.deepcopy(room['settings'])
        settings['teams'][0]['members']=[0]
        with self.assertRaises(GameError):a.validate_settings(settings,room['players'])
        settings=copy.deepcopy(room['settings']);settings['teams'][1]['members']=[1,2,3]
        with self.assertRaises(GameError):a.validate_settings(settings,room['players'])
        settings=copy.deepcopy(room['settings']);settings['teams'][0]['first_presenter']=1
        room['phase']='lobby';room['settings']=settings;a.start(room,rng=random.Random(2))
        self.assertEqual(room['active']['presenter'],1)
        with self.assertRaises(GameError):a.validate_settings(dict(mode='individual',precision='Точно'),room['players'])

    def test_category_cyclic_and_start_choice_each_time(self):
        self.assertIsNone(a.cell_category(0));self.assertIsNone(a.cell_category(49))
        self.assertEqual([a.cell_category(i) for i in range(1,7)],[c['key'] for c in CATEGORIES])
        self.assertEqual(a.cell_category(7),'drawA')
        room=make_room();p=prepare(room,category='mimeB')
        self.assertEqual(room['active']['category'],'mimeB')
        apply(room,'failure',None)
        a.host_act(room,dict(action='next'))
        prepare(room,category='explainB')
        self.assertEqual(room['active']['category'],'explainB')
        self.assertEqual(room['positions'],[0,0])

    def test_normal_success_failure_violation_and_rotation(self):
        room=make_room();prepare(room,4);room['active']['open']=False
        apply(room,guesser=1)
        self.assertEqual(room['positions'],[4,0])
        self.assertEqual(room['offsets'],[1,0]);self.assertEqual(room['cursor'],1)
        a.host_act(room,dict(action='next'));self.assertEqual(room['active']['presenter'],2)
        prepare(room);apply(room,'violation',None)
        self.assertEqual(room['positions'],[4,0]);self.assertEqual(room['offsets'],[1,1])
        a.host_act(room,dict(action='next'));self.assertEqual(room['active']['presenter'],1)
        prepare(room);apply(room,'failure',None)
        self.assertEqual(room['offsets'],[0,1])

    def test_open_round_both_branches_and_violation(self):
        for guesser,expected in [(1,[6,0]),(2,[2,4])]:
            room=make_room();prepare(room,5);room['active']['open']=True
            apply(room,guesser=guesser)
            self.assertEqual(room['positions'],expected)
        room=make_room();prepare(room);room['active']['open']=True
        apply(room,'violation',None)
        self.assertEqual(room['positions'],[0,0])

    def test_individual_two_recipients_and_no_collision(self):
        room=make_room('individual',3);prepare(room,5)
        room['positions']=[3,0,8]
        apply(room,guesser=1)
        self.assertEqual(room['positions'],[8,5,8])
        self.assertTrue(room['history'][-1]['open'])
        self.assertEqual(room['offsets'],[0,0,0])
        with self.assertRaises(GameError):a.resolve_turn(room,dict(outcome='success',guesser=room['active']['presenter']))

    def test_nonrecursive_occupancy_and_shared_cells(self):
        tx=a.build_move_transaction([1,5,5,4],{0:4},'example')
        self.assertEqual(tx['after'],[5,4,4,4])
        self.assertEqual(tx['bumped'],[1,2])
        self.assertEqual(a.build_move_transaction([0,0,49],{0:0},'start')['after'],[0,0,49])
        tx=a.build_move_transaction([3,1,5,6],{0:2,1:4},'same')
        self.assertEqual(tx['after'],[5,5,4,6])
        tx=a.build_move_transaction([3,5,7],{0:2,1:4},'departed')
        self.assertEqual(tx['after'],[5,9,7])
        self.assertEqual(a.build_move_transaction([1,5],{0:8},'passing')['after'],[9,5])

    def test_simultaneous_finish_overshoot_and_joint_winners(self):
        tx=a.build_move_transaction([48,46,49],{0:5,1:4},'finish')
        self.assertEqual(tx['after'],[49,49,49]);self.assertEqual(tx['raw_destinations'][0],53)
        self.assertEqual(tx['winners'],[0,1,2]);self.assertEqual(tx['bumped'],[])
        room=make_room();prepare(room);room['positions']=[47,45];room['active']['open']=True
        apply(room,guesser=2)
        self.assertEqual(room['phase'],'finished');self.assertEqual(room['winners'],[0,1])

    def test_preview_corrections_are_pure_and_apply_once(self):
        room=make_room();prepare(room);room['active']['open']=True;room['phase']='review'
        original=copy.deepcopy(room['positions'])
        a.host_act(room,dict(action='result',outcome='success',guesser=2))
        self.assertEqual(room['positions'],original)
        self.assertEqual(a.view(room)['preview']['after'],[2,4])
        a.host_act(room,dict(action='result',outcome='failure',guesser=None))
        a.host_act(room,dict(action='apply'))
        self.assertEqual(room['positions'],original)
        with self.assertRaises(GameError):a.host_act(room,dict(action='apply'))
        self.assertEqual(len(room['history']),1)

    def test_deck_exhaustion_and_undo_restore_complete_turn(self):
        room=make_room();deck=room['decks']['3'];deck['discard']=deck['draw'];deck['draw']=[]
        before=copy.deepcopy({k:room[k] for k in ['decks','positions','cursor','offsets','history']})
        room['checkpoint']=a.snapshot(room)
        prepare(room);apply(room,'failure',None)
        self.assertEqual(len(room['decks']['3']['draw']),29)
        self.assertEqual(len(room['decks']['3']['discard']),1)
        a.host_act(room,dict(action='undo'))
        for key,value in before.items():self.assertEqual(room[key],value,key)
        self.assertEqual(room['phase'],'handoff');self.assertIsNone(room['active']['card'])
        a.validate(room)

    def test_undo_also_rolls_back_unfinished_following_attempt(self):
        room=make_room();prepare(room);apply(room,'failure',None)
        a.host_act(room,dict(action='next'));prepare(room,5)
        a.host_act(room,dict(action='undo'))
        self.assertEqual(len(room['history']),0);self.assertEqual(room['cursor'],0)
        self.assertEqual(sum(len(v['draw']) for v in room['decks'].values()),90)

    def test_deadlines_and_secret_visibility(self):
        room=make_room();p=prepare(room);word=CARD_BY_ID[room['active']['card']]['tasks'][room['active']['category']]['text']
        room['active']['open']=False
        self.assertNotIn(word,json.dumps(a.view(room),ensure_ascii=False))
        self.assertEqual(a.view(room,p)['secret_task']['text'],word)
        self.assertIsNone(a.view(room,1)['secret_task'])
        a.act(room,p,dict(action='opponents'),now=100)
        self.assertIsNotNone(a.view(room,2)['opponent_card']);self.assertIsNone(a.view(room,1)['opponent_card'])
        self.assertIsNone(a.view(room)['opponent_card'])
        a.act(room,p,dict(action='ready'),now=101)
        a.act(room,p,dict(action='perform'),now=102)
        self.assertEqual(room['deadline'],162)
        self.assertIsNone(a.view(room,p)['secret_task'])
        self.assertFalse(a.expire(room,161.9));self.assertTrue(a.expire(room,162))
        self.assertEqual(room['phase'],'review');self.assertTrue(room['active']['expired'])
        self.assertFalse(a.expire(room,200))
        # A timely spoken answer may still be confirmed by humans after the timer.
        apply(room,guesser=1);self.assertGreater(room['positions'][0],0)

    def test_memorize_expiry_and_surrender_do_not_redraw(self):
        room=make_room();p=prepare(room);card=room['active']['card']
        a.act(room,p,dict(action='memorize'),now=100)
        self.assertTrue(a.expire(room,111));self.assertEqual(room['phase'],'ready')
        with self.assertRaises(GameError):a.act(room,p,dict(action='difficulty',difficulty=5))
        a.act(room,p,dict(action='give_up'));self.assertEqual(room['phase'],'review')
        a.host_act(room,dict(action='apply'));self.assertEqual(room['positions'],[0,0])
        self.assertEqual(room['decks']['3']['discard'],[card])

    def test_open_card_never_shown_to_opponents(self):
        room=make_room();p=prepare(room);room['active']['open']=True
        with self.assertRaises(GameError):a.act(room,p,dict(action='opponents'))
        self.assertIsNone(a.view(room,2)['opponent_card'])

    def test_drawing_permission_chunk_dedup_undo_and_clear(self):
        room=make_room();p=prepare(room,category='drawA');a.act(room,p,dict(action='ready'));a.act(room,p,dict(action='perform'),now=100)
        data=dict(action='draw',kind='stroke',match=room['match'],turn_id=room['active']['id'],op_id='x0',stroke_id='s1',seq=0,tool='pen',width=7,points=[[.1,.2],[.4,.5]])
        a.act(room,p,data);a.act(room,p,data)
        self.assertEqual(len(room['drawing']['strokes'][0]['points']),2)
        with self.assertRaises(GameError):a.act(room,1,data)
        bad=dict(data,op_id='bad',stroke_id='bad',points=[[float('nan'),.2]])
        with self.assertRaises(GameError):a.act(room,p,bad)
        undo=dict(action='draw',kind='undo',match=room['match'],turn_id=room['active']['id'],op_id='undo')
        a.act(room,p,undo);a.act(room,p,undo);self.assertEqual(room['drawing']['strokes'],[])
        a.act(room,p,data);self.assertEqual(room['drawing']['strokes'],[])
        a.act(room,p,dict(data,op_id='x2',stroke_id='s2'))
        a.act(room,p,dict(undo,kind='clear',op_id='clear'))
        self.assertEqual(room['drawing']['strokes'],[])

    def test_corrupt_save_reports_error_without_reset(self):
        room=make_room();old=copy.deepcopy(room);room['positions'][0]=-1
        with self.assertRaisesRegex(GameError,'повреждено'):a.view(room)
        self.assertEqual(room['positions'][0],-1)
        room=old;room['phase']='performing';room['deadline']=None
        with self.assertRaises(GameError):a.view(room)

    def test_full_team_cycle_open_round_and_cancel(self):
        room=make_room();initial=copy.deepcopy(room['decks'])
        prepare(room,3);room['active']['open']=True;apply(room,guesser=2)
        self.assertEqual(room['positions'],[2,4])
        a.host_act(room,dict(action='undo'));self.assertEqual(room['decks'],initial)
        prepare(room,4);room['active']['open']=True;apply(room,guesser=1)
        a.host_act(room,dict(action='next'));self.assertEqual(room['active']['presenter'],2)
        prepare(room,3);room['active']['open']=False;apply(room,guesser=3)
        a.host_act(room,dict(action='next'));self.assertEqual(room['active']['presenter'],1)
        self.assertEqual(len(room['history']),2);self.assertEqual(room['positions'],[6,3])
        a.validate(room)


class ActivityAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        self.app.config.update(TESTING=True,GAMES_DB_PATH=str(Path(self.temp.name)/'games.sqlite3'),GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp);self.app.register_blueprint(main_bp);self.client=self.app.test_client()
        response=self.client.post('/api/games/rooms',json={'game':'activity'});self.assertEqual(response.status_code,201)
        self.room=response.json;self.base='/api/games/rooms/'+self.room['code'];self.host={'Authorization':'Bearer '+self.room['token']}
        self.players=[]
        for i in range(4):
            response=self.client.post(self.base+'/join',json={'name':f'Игрок {i}'})
            self.players.append({'Authorization':'Bearer '+response.json['token']})
        self.settings=dict(mode='teams',teams=[dict(name='Луна',members=[0,1],first_presenter=0),dict(name='Солнце',members=[2,3],first_presenter=2)],starting_side=0,precision='Согласуем точный смысл')
    def tearDown(self):self.temp.cleanup()
    def state(self,headers=None):return self.client.get(self.base,headers=headers or self.host).json
    def action(self,name,headers=None,**extra):
        state=self.state(headers)
        return self.client.post(self.base+'/action',headers=headers or self.host,json=dict(action=name,revision=state['revision'],match=state['match'],turn_id=state['turn_id'],**extra))
    def start(self):
        self.assertEqual(self.action('configure',settings=self.settings).status_code,200)
        self.assertEqual(self.action('start').status_code,200)
    def prepare(self):
        p=self.state()['presenter'];headers=self.players[p]
        self.assertEqual(self.action('take_turn',headers).status_code,200)
        self.assertEqual(self.action('difficulty',headers,difficulty=3).status_code,200)
        if self.state()['phase']=='choose_task':self.assertEqual(self.action('task',headers,category='drawA').status_code,200)
        return headers

    def test_join_routes_capacity_and_configuration(self):
        self.assertIn('/games/activity/',self.room['url'])
        self.assertEqual(self.client.get(self.room['url']).status_code,200)
        self.assertIn(b'activity.js',self.client.get('/games/join/'+self.room['code']).data)
        self.assertEqual(self.client.get(self.base+'/qr.svg').status_code,200)
        self.assertEqual(self.action('start').status_code,400)
        self.assertEqual(self.action('configure',self.players[0],settings=self.settings).status_code,400)
        self.start()
        self.assertEqual(self.client.post(self.base+'/join',json={'name':'late'}).status_code,400)
        self.assertEqual(self.client.get(self.base).status_code,400)

    def test_network_privacy_controlled_preview_and_clock(self):
        self.start();headers=self.prepare()
        private=self.state(headers);word=private['secret_task']['text']
        for viewer in [self.host,self.players[1],self.players[2]]:
            response=self.state(viewer)
            self.assertIsNone(response['secret_task']);self.assertIsNone(response['card_options'])
            self.assertNotIn(word,json.dumps(response,ensure_ascii=False))
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.room['code']);room['active']['open']=False;save_room(connection,self.room['code'],room)
        self.assertEqual(self.action('opponents',headers).status_code,200)
        self.assertIsNotNone(self.state(self.players[2])['opponent_card'])
        self.assertIsNone(self.state(self.players[1])['opponent_card'])
        self.assertIsNone(self.state()['opponent_card'])
        self.assertEqual(self.action('ready',headers).status_code,200)
        self.assertEqual(self.action('perform',headers).status_code,200)
        self.assertIsNone(self.state(headers)['secret_task'])
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.room['code']);room['deadline']=1;save_room(connection,self.room['code'],room)
        self.assertEqual(self.state()['phase'],'review');self.assertTrue(self.state()['expired'])
        self.assertEqual(self.action('result',outcome='success',guesser=0).status_code,400)

    def test_duplicate_apply_concurrent_and_undo_restores_persistence(self):
        self.start()
        with self.app.app_context(),database() as connection:
            before=copy.deepcopy(load_room(connection,self.room['code'])['decks'])
        headers=self.prepare();self.action('give_up',headers)
        state=self.state();body=dict(action='apply',revision=state['revision'])
        def apply_once(_):
            with self.app.test_client() as client:return client.post(self.base+'/action',headers=self.host,json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(apply_once,range(2)))
        self.assertEqual(sorted(statuses),[200,409]);self.assertEqual(len(self.state()['history']),1)
        self.assertEqual(self.action('undo').status_code,200)
        with self.app.app_context(),database() as connection:
            after=load_room(connection,self.room['code']);self.assertEqual(after['decks'],before)
        other=Flask('restarted');other.config.update(TESTING=True,GAMES_DB_PATH=self.app.config['GAMES_DB_PATH']);other.register_blueprint(games_bp)
        restored=other.test_client().get(self.base,headers=self.players[0]).json
        self.assertEqual(restored['phase'],'handoff');self.assertIsNone(restored['secret_task']);self.assertEqual(restored['presenter'],0)

    def test_live_drawing_is_scoped_and_duplicate_chunks_do_not_repeat(self):
        self.start();headers=self.prepare();self.action('ready',headers);self.action('perform',headers)
        state=self.state(headers)
        op=dict(action='draw',match=state['match'],turn_id=state['turn_id'],op_id='segment-0',kind='stroke',stroke_id='stroke',seq=0,tool='pen',width=7,points=[[.1,.2],[.3,.4]])
        for _ in range(2):self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=op).status_code,200)
        self.assertEqual(len(self.state()['drawing']['strokes'][0]['points']),2)
        self.assertEqual(self.client.post(self.base+'/action',headers=self.players[1],json=dict(op,op_id='other')).status_code,400)
        self.assertEqual(self.action('stop').status_code,200)
        self.assertEqual(self.client.post(self.base+'/action',headers=headers,json=dict(op,op_id='late',seq=1)).status_code,400)
        self.assertEqual(self.action('result',outcome='failure',guesser=None).status_code,200)
        self.assertEqual(self.action('apply').status_code,200)
        self.assertEqual(self.action('undo').status_code,200)
        self.assertNotEqual(self.state()['turn_id'],op['turn_id'])

    def test_corrupt_save_is_not_overwritten(self):
        self.start()
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.room['code']);room['positions'][0]=-20;save_room(connection,self.room['code'],room)
        response=self.client.get(self.base,headers=self.host)
        self.assertEqual(response.status_code,400)
        self.assertIn('повреждено',response.json['error'])

    def test_full_api_circle_with_open_round_and_undo(self):
        self.start();headers=self.prepare()
        with self.app.app_context(),database() as connection:
            room=load_room(connection,self.room['code']);room['active']['open']=True;save_room(connection,self.room['code'],room)
        self.action('ready',headers);self.action('perform',headers);self.action('stop')
        self.action('result',outcome='success',guesser=2);self.action('apply')
        self.assertEqual(self.state()['positions'],[2,4])
        self.action('undo');headers=self.prepare();self.action('give_up',headers);self.action('apply');self.action('next')
        self.assertEqual(self.state()['presenter'],2)
        headers=self.prepare();self.action('give_up',headers);self.action('apply');self.action('next')
        self.assertEqual(self.state()['presenter'],1)
        self.assertEqual(len(self.state()['history']),2)
