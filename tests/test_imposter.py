import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from app import imposter
from app.durak import GameError
from app.game_routes import games_bp
from app.routes import main_bp


class ImposterRulesTests(unittest.TestCase):
    def room(self, count=4):
        room = imposter.new_room()
        room['players'] = [dict(name=f'Игрок {i}', token=f'private-{i}', seen=0) for i in range(count)]
        room['host'] = 'host-secret'
        return room

    def reveal(self, room):
        imposter.start(room)
        for i in range(len(room['players'])):
            imposter.act(room, i, {'action':'ready'})

    def discussion(self, room):
        self.reveal(room)
        for i in room['order']:
            imposter.act(room, i, {'action':'clue', 'text':'Ассоциация'})
        self.assertEqual(room['phase'], 'discussion')

    def convict(self, room, target):
        imposter.host_act(room, {'action':'voting'})
        for i in range(len(room['players'])):
            choice = target if i != target else (i + 1) % len(room['players'])
            imposter.act(room, i, {'action':'vote', 'target':choice})

    def test_role_privacy_and_readiness(self):
        room = self.room()
        imposter.start(room)
        host = imposter.view(room)
        for forbidden in ('word', 'role', 'imposter', 'votes', 'host', 'token'):
            self.assertNotIn(forbidden, host)
        self.assertNotIn('private-', json.dumps(host))
        for i in range(4):
            private = imposter.view(room, i)
            if i == room['imposter']:
                self.assertEqual(private['role'], 'imposter')
                self.assertNotIn('word', private)
            else:
                self.assertEqual(private['word'], room['word'])
                self.assertEqual(private['role'], 'team')
        imposter.act(room, 0, {'action':'ready'})
        imposter.act(room, 0, {'action':'ready'})
        self.assertEqual(room['ready'], [0])
        for i in range(1, 4): imposter.act(room, i, {'action':'ready'})
        self.assertEqual(room['phase'], 'clues')

    def test_minimum_order_word_validation_and_three_round_limit(self):
        with self.assertRaises(GameError): imposter.start(self.room(2))
        with self.assertRaises(GameError): imposter.start(self.room(13))
        room = self.room()
        self.reveal(room)
        first = room['order'][0]
        with self.assertRaises(GameError): imposter.act(room, (first+1)%4, {'action':'clue','text':'Один'})
        for value in [None, 1, '', 'x'*33, '<script>', room['word'], '123']:
            with self.assertRaises(GameError): imposter.act(room, first, {'action':'clue','text':value})
        imposter.host_act(room, {'action':'skip'})
        for i in room['order'][1:]: imposter.act(room, i, {'action':'clue','text':'Подсказка'})
        self.assertEqual(room['clues'][0]['text'], 'Пропуск')
        for number in (2, 3):
            imposter.host_act(room, {'action':'another_round'})
            self.assertEqual(room['round'], number)
            for i in room['order']: imposter.act(room, i, {'action':'clue','text':'Ещё подсказка'})
        with self.assertRaises(GameError): imposter.host_act(room, {'action':'another_round'})
        self.assertEqual(len(room['clues']), 12)

    def test_private_ballots_and_runoff(self):
        room = self.room()
        self.discussion(room)
        imposter.host_act(room, {'action':'voting'})
        with self.assertRaises(GameError): imposter.act(room, 0, {'action':'vote','target':0})
        with self.assertRaises(GameError): imposter.act(room, 0, {'action':'vote','target':True})
        imposter.act(room, 0, {'action':'vote','target':1})
        with self.assertRaises(GameError): imposter.act(room, 0, {'action':'vote','target':2})
        self.assertEqual(imposter.view(room, 0)['own_vote'], 1)
        self.assertIsNone(imposter.view(room, 1)['own_vote'])
        self.assertNotIn('tally', imposter.view(room))
        for i, target in [(1,0),(2,1),(3,0)]: imposter.act(room, i, {'action':'vote','target':target})
        self.assertEqual(room['ballot'], 2)
        self.assertEqual(room['candidates'], [0,1])
        self.assertEqual(room['votes'], {})
        with self.assertRaises(GameError): imposter.act(room, 0, {'action':'vote','target':2})
        for i, target in [(0,1),(1,0),(2,1),(3,0)]: imposter.act(room, i, {'action':'vote','target':target})
        self.assertEqual(room['phase'], 'finished')
        self.assertEqual(room['winner'], 'imposter')

    def test_wrong_accusation_and_rematch(self):
        room = self.room()
        self.discussion(room)
        civilian = next(i for i in range(4) if i != room['imposter'])
        self.convict(room, civilian)
        self.assertEqual(room['winner'], 'imposter')
        result = imposter.view(room)
        self.assertEqual(result['word'], room['word'])
        self.assertEqual(result['imposter'], room['imposter'])
        old_match = room['match']
        imposter.start(room)
        self.assertEqual(room['match'], old_match + 1)
        self.assertEqual(room['votes'], {})
        self.assertNotIn('word', imposter.view(room))

    def test_imposter_final_guess_is_unique_and_normalized(self):
        for correct in (True, False):
            room = self.room()
            self.discussion(room)
            self.convict(room, room['imposter'])
            self.assertEqual(room['phase'], 'guess')
            self.assertNotIn('word', imposter.view(room))
            with self.assertRaises(GameError): imposter.act(room, (room['imposter']+1)%4, {'action':'guess','text':room['word']})
            room['word'] = 'Ёж'
            imposter.act(room, room['imposter'], {'action':'guess', 'text':' ЕЖ! ' if correct else 'Панда'})
            self.assertEqual(room['winner'], 'imposter' if correct else 'team')
            with self.assertRaises(GameError): imposter.act(room, room['imposter'], {'action':'guess','text':'Ёж'})

    def test_final_guess_deadline(self):
        room = self.room()
        self.discussion(room)
        self.convict(room, room['imposter'])
        with patch('app.imposter.time.time', return_value=room['deadline'] + 1):
            self.assertTrue(imposter.expire(room))
        self.assertEqual(room['winner'], 'team')
        self.assertFalse(imposter.expire(room))


class ImposterAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
        self.app.config.update(TESTING=True, GAMES_DB_PATH=str(Path(self.temp.name)/'games.sqlite3'), GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp)
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()
        created = self.client.post('/api/games/rooms', json={'game':'imposter'})
        self.assertEqual(created.status_code, 201)
        self.room = created.json
        self.base = '/api/games/rooms/' + self.room['code']
        self.host = {'Authorization':'Bearer '+self.room['token']}

    def tearDown(self): self.temp.cleanup()

    def join(self, name):
        result = self.client.post(self.base+'/join', json={'name':name})
        self.assertEqual(result.status_code, 200)
        return {'Authorization':'Bearer '+result.json['token']}

    def state(self, headers=None): return self.client.get(self.base, headers=headers or self.host).json

    def action(self, headers, action, **extra):
        state = self.state(headers)
        return self.client.post(self.base+'/action', headers=headers, json=dict(action=action, revision=state['revision'], match=state['match'], phase=state['phase'], ballot=state['ballot'], **extra))

    def test_routes_generic_join_and_game_isolation(self):
        self.assertIn('/games/imposter/', self.room['url'])
        self.assertEqual(self.client.get(self.room['url']).status_code, 200)
        joined = self.client.get('/games/join/'+self.room['code'])
        self.assertIn(b'imposter.js', joined.data)
        manual = self.client.get('/games/join?code='+self.room['code'].lower())
        self.assertEqual(manual.status_code, 302)
        self.assertTrue(manual.location.endswith(self.room['code']))
        self.assertEqual(self.client.get('/games/join/ABSENT').status_code, 400)
        self.assertEqual(self.client.get(self.base+'/qr.svg').status_code, 200)
        self.assertEqual(self.client.post('/api/games/rooms', json={'game':'unknown'}).status_code, 400)
        self.assertEqual(self.client.get('/games').status_code, 200)
        self.assertEqual(self.client.get(self.base).status_code, 400)

    def test_concurrent_readiness_and_votes_survive_revision_changes(self):
        players = [self.join(str(i)) for i in range(3)]
        self.assertEqual(self.action(self.host,'start').status_code, 200)
        state = self.state()
        common = dict(revision=state['revision'], match=state['match'], phase=state['phase'], ballot=state['ballot'])
        for headers in players:
            self.assertEqual(self.client.post(self.base+'/action', headers=headers, json=dict(action='ready',**common)).status_code, 200)
        self.assertEqual(self.state()['phase'], 'clues')
        for index in self.state()['order']:
            self.assertEqual(self.action(players[index],'clue',text='Подсказка').status_code, 200)
        self.assertEqual(self.action(self.host,'voting').status_code, 200)
        state = self.state()
        common = dict(revision=state['revision'], match=state['match'], phase=state['phase'], ballot=state['ballot'])
        for i, headers in enumerate(players):
            self.assertEqual(self.client.post(self.base+'/action', headers=headers, json=dict(action='vote', target=(i+1)%3, **common)).status_code, 200)
        self.assertEqual(self.state()['ballot'], 2)
        self.assertEqual(self.client.post(self.base+'/action', headers=players[0], json=dict(action='vote',target=1,**common)).status_code, 409)

    def test_capacity_revoke_and_permissions(self):
        players = [self.join(str(i)) for i in range(12)]
        self.assertEqual(self.client.post(self.base+'/join', json={'name':'extra'}).status_code, 400)
        self.assertEqual(self.action(players[0],'start').status_code, 400)
        self.assertEqual(self.action(self.host,'remove',target=0).status_code, 200)
        self.assertEqual(self.client.get(self.base,headers=players[0]).status_code, 400)
        self.assertEqual(self.action(self.host,'start').status_code, 200)
        self.assertEqual(self.action(players[1],'skip').status_code, 400)
        self.assertEqual(self.action(self.host,'reset').status_code, 200)
        self.assertEqual(self.state(players[1])['phase'], 'lobby')
        self.assertNotIn('role', self.state(players[1]))

    def test_timeout_resolves_without_imposter_returning(self):
        from app.game_routes import database, load_room, save_room
        players = [self.join(str(i)) for i in range(3)]
        self.action(self.host,'start')
        with self.app.app_context(), database() as db:
            room = load_room(db,self.room['code'])
            room.update(phase='guess', deadline=time.time()-1)
            save_room(db,self.room['code'],room)
        state = self.state()
        self.assertEqual(state['phase'], 'finished')
        self.assertEqual(state['winner'], 'team')
        self.assertEqual(self.state()['revision'], state['revision'])
