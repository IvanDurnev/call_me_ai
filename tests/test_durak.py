import json
import random
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
from app.durak import GameError, act, beats, new_room, rank, start, view, finish_round
from app.game_routes import games_bp


class DurakRulesTests(unittest.TestCase):
    def room(self, n=2):
        room = new_room()
        room['players'] = [dict(name=f'P{i}', hand=[], token=f'secret{i}', seen=0) for i in range(n)]
        room['host'] = 'private-host'
        start(room)
        return room

    def test_deal_and_private_views(self):
        for n in range(2, 7):
            room = self.room(n)
            cards = room['deck'] + [c for p in room['players'] for c in p['hand']]
            self.assertEqual(len(set(cards)), 36)
            self.assertEqual(len(room['deck']), 36 - 6 * n)
            self.assertTrue(all(len(p['hand']) == 6 for p in room['players']))
            self.assertNotIn('host', view(room))
            self.assertNotIn('token', json.dumps(view(room)))
            self.assertEqual(view(room)['hand'], [])
            self.assertEqual(view(room, 1)['hand'], room['players'][1]['hand'])

    def test_trump_rules(self):
        self.assertTrue(beats('H6', 'S14', 'H'))
        self.assertTrue(beats('S10', 'S9', 'H'))
        self.assertFalse(beats('S14', 'H6', 'H'))
        self.assertFalse(beats('C14', 'S6', 'H'))
        self.assertFalse(beats('H6', 'H7', 'H'))

    def fixed(self):
        room = self.room(3)
        room.update(attacker=0, defender=1, limit=3, trump='H6', table=[], deck=['C8', 'C9', 'C10'])
        for p, hand in zip(room['players'], [['S6','D6','S7'], ['S8','H7','C6'], ['D8','S9','H8']]):
            p['hand'] = hand
        return room

    def test_attack_defend_throw_and_pass(self):
        room = self.fixed()
        with self.assertRaises(GameError): act(room, 2, {'action': 'attack', 'card': 'D8'})
        act(room, 0, {'action': 'attack', 'card': 'S6'})
        with self.assertRaises(GameError): act(room, 2, {'action': 'attack', 'card': 'S9'})
        with self.assertRaises(GameError): act(room, 0, {'action': 'pass'})
        with self.assertRaises(GameError): act(room, 1, {'action': 'defend', 'target': 0, 'card': 'C6'})
        act(room, 1, {'action': 'defend', 'target': 0, 'card': 'S8'})
        act(room, 0, {'action': 'pass'})
        self.assertEqual(room['passed'], [0])
        act(room, 2, {'action': 'attack', 'card': 'D8'})
        self.assertEqual(room['passed'], [])
        act(room, 1, {'action': 'defend', 'target': 1, 'card': 'H7'})
        act(room, 0, {'action': 'pass'})
        act(room, 2, {'action': 'pass'})
        self.assertEqual(room['discard'], 4)
        self.assertEqual(room['attacker'], 1)

    def test_taking_and_throwing_after_take(self):
        room = self.fixed()
        act(room, 0, {'action': 'attack', 'card': 'S6'})
        act(room, 1, {'action': 'take'})
        act(room, 0, {'action': 'attack', 'card': 'D6'})
        act(room, 0, {'action': 'pass'})
        act(room, 2, {'action': 'pass'})
        self.assertIn('S6', room['players'][1]['hand'])
        self.assertIn('D6', room['players'][1]['hand'])
        self.assertEqual(room['attacker'], 2)
        self.assertEqual(room['discard'], 0)

    def test_limit_and_invalid_defense_target(self):
        room = self.fixed()
        room['limit'] = 1
        act(room, 0, {'action': 'attack', 'card': 'S6'})
        with self.assertRaises(GameError): act(room, 0, {'action': 'attack', 'card': 'D6'})
        with self.assertRaises(GameError): act(room, 1, {'action': 'defend', 'target': True, 'card': 'S8'})
        with self.assertRaises(GameError): act(room, 1, {'action': 'defend', 'target': -1, 'card': 'S8'})
        act(room, 1, {'action': 'defend', 'target': 0, 'card': 'S8'})
        self.assertEqual(room['table'], [])
        self.assertEqual(room['attacker'], 1)

    def test_last_cards_draw_and_rematch(self):
        room = self.room(2)
        room.update(deck=[], attacker=0, defender=1, limit=1, table=[], winners=[])
        room['players'][0]['hand'] = ['S6']
        room['players'][1]['hand'] = ['S7']
        act(room, 0, {'action': 'attack', 'card': 'S6'})
        act(room, 1, {'action': 'defend', 'target': 0, 'card': 'S7'})
        self.assertEqual(room['phase'], 'finished')
        self.assertIsNone(room['loser'])
        start(room)
        self.assertEqual(room['phase'], 'playing')
        self.assertEqual(room['winners'], [])

    def test_loser_and_eliminated_player_skipped(self):
        room = self.fixed()
        room.update(deck=[], table=[['S6', 'S8']], taking=False)
        room['players'][0]['hand'] = []
        room['players'][1]['hand'] = []
        room['players'][2]['hand'] = ['D8']
        finish_round(room)
        self.assertEqual(room['phase'], 'finished')
        self.assertEqual(room['loser'], 2)

    def test_random_complete_games_preserve_all_cards(self):
        rng = random.Random(42)
        for n in range(2, 7):
            for _ in range(8):
                room = self.room(n)
                for step in range(4000):
                    if room['phase'] == 'finished': break
                    candidates = []
                    for i, player in enumerate(room['players']):
                        if i in room['winners']: continue
                        if i == room['defender']:
                            if room['table'] and not room['taking']:
                                candidates.append((i, {'action': 'take'}))
                                for target, pair in enumerate(room['table']):
                                    if not pair[1]:
                                        candidates.extend((i, {'action':'defend', 'target':target, 'card':c}) for c in player['hand'] if beats(c, pair[0], room['trump'][0]))
                        else:
                            table_ranks = {rank(c) for pair in room['table'] for c in pair if c}
                            if len(room['table']) < room['limit']:
                                candidates.extend((i, {'action':'attack', 'card':c}) for c in player['hand'] if (rank(c) in table_ranks if room['table'] else i == room['attacker']))
                            if room['table'] and (room['taking'] or all(p[1] for p in room['table'])) and i not in room['passed']:
                                candidates.append((i, {'action':'pass'}))
                    self.assertTrue(candidates, (n, step, room))
                    # Prefer legal defense over taking so games do not run indefinitely.
                    defenses = [c for c in candidates if c[1]['action'] == 'defend']
                    i, move = rng.choice(defenses or candidates)
                    act(room, i, move)
                    live = room['deck'] + [c for p in room['players'] for c in p['hand']] + [c for pair in room['table'] for c in pair if c]
                    self.assertEqual(len(set(live)), len(live))
                    self.assertEqual(len(live) + room['discard'], 36)
                self.assertEqual(room['phase'], 'finished', (n, step))


class DurakAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        self.app.config.update(TESTING=True, GAMES_DB_PATH=str(Path(self.temp.name) / 'games.sqlite3'), GAMES_PUBLIC_URL='https://example.com')
        self.app.register_blueprint(games_bp)
        self.client = self.app.test_client()
        self.room = self.client.post('/api/games/rooms').json
        self.base = '/api/games/rooms/' + self.room['code']
        self.host = {'Authorization': 'Bearer ' + self.room['token']}

    def tearDown(self): self.temp.cleanup()

    def join(self, name):
        result = self.client.post(self.base + '/join', json={'name':name})
        self.assertEqual(result.status_code, 200)
        return {'Authorization': 'Bearer ' + result.json['token']}

    def test_qr_join_auth_and_privacy(self):
        alice, bob = self.join('Алиса'), self.join('Боб')
        self.assertEqual(self.client.get(self.base).status_code, 400)
        public = self.client.get(self.base, headers=self.host).json
        self.assertEqual(public['join_url'], 'https://example.com/games/join/' + self.room['code'])
        self.assertNotIn('host', public)
        self.assertNotIn('token', json.dumps(public))
        revision = public['revision']
        self.assertEqual(self.client.post(self.base+'/action', headers=alice, json={'action':'start','revision':revision}).status_code, 400)
        self.assertEqual(self.client.post(self.base+'/action', headers=self.host, json={'action':'start','revision':revision}).status_code, 200)
        a = self.client.get(self.base, headers=alice).json
        b = self.client.get(self.base, headers=bob).json
        h = self.client.get(self.base, headers=self.host).json
        self.assertEqual(len(a['hand']), 6)
        self.assertEqual(set(a['hand']) & set(b['hand']), set())
        self.assertEqual(h['hand'], [])
        self.assertNotIn('deck', h)
        self.assertEqual(self.client.post(self.base+'/action', headers=alice, json={'action':'take','revision':revision}).status_code, 409)
        self.assertEqual(self.client.post(self.base+'/join', json={'name':'Опоздал'}).status_code, 400)
        qr = self.client.get(self.base + '/qr.svg')
        self.assertEqual(qr.status_code, 200)
        self.assertIn(b'<svg', qr.data)
        self.assertEqual(qr.headers['Cache-Control'], 'no-store')

    def test_remove_revoke_capacity_and_names(self):
        alice = self.join('Алиса')
        self.assertEqual(self.client.post(self.base+'/join', json={'name':'алиса'}).status_code, 400)
        self.assertEqual(self.client.post(self.base+'/join', json={'name':'x'*25}).status_code, 400)
        revision = self.client.get(self.base, headers=self.host).json['revision']
        self.assertEqual(self.client.post(self.base+'/action', headers=self.host, json={'action':'remove','target':0,'revision':revision}).status_code, 200)
        self.assertEqual(self.client.get(self.base, headers=alice).status_code, 400)
        for i in range(6): self.join(str(i))
        self.assertEqual(self.client.post(self.base+'/join', json={'name':'Седьмой'}).status_code, 400)

    def test_room_shared_between_app_instances_and_concurrent_joins(self):
        def join(i):
            with self.app.test_client() as client:
                return client.post(self.base+'/join', json={'name':str(i)}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(join, range(8)))
        self.assertEqual(results.count(200), 6)
        other = Flask('other')
        other.config.update(TESTING=True, GAMES_DB_PATH=self.app.config['GAMES_DB_PATH'])
        other.register_blueprint(games_bp)
        self.assertEqual(len(other.test_client().get(self.base, headers=self.host).json['players']), 6)

    def test_host_can_reset_without_losing_players_or_credentials(self):
        alice = self.join('Алиса')
        self.join('Боб')
        revision = self.client.get(self.base, headers=self.host).json['revision']
        self.client.post(self.base+'/action', headers=self.host, json={'action':'start','revision':revision})
        revision += 1
        self.assertEqual(self.client.post(self.base+'/action', headers=alice, json={'action':'reset','revision':revision}).status_code, 400)
        self.assertEqual(self.client.post(self.base+'/action', headers=self.host, json={'action':'reset','revision':revision}).status_code, 200)
        state = self.client.get(self.base, headers=alice).json
        self.assertEqual(state['phase'], 'lobby')
        self.assertEqual(state['hand'], [])
        self.assertEqual(len(state['players']), 2)
        self.assertEqual(state['revision'], revision + 1)

    def test_expired_room_and_json_errors(self):
        import sqlite3
        self.assertEqual(self.client.post(self.base+'/join', json=[]).status_code, 400)
        with sqlite3.connect(self.app.config['GAMES_DB_PATH']) as connection:
            connection.execute('UPDATE rooms SET touched=0')
        self.assertEqual(self.client.get(self.base, headers=self.host).status_code, 400)
        self.assertEqual(self.client.get(self.base+'/qr.svg').status_code, 400)
