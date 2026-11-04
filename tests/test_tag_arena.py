import copy
import json
import math
import random
import unittest
from unittest.mock import patch

from app import tag, tag_arena as arena


class TagArenaTests(unittest.TestCase):
    def room(self,round_number=1,count=2):
        room=tag.new_room()
        for i in range(count):
            player=dict(name=f'Игрок {i}',token=f'private-{i}')
            room['players'].append(player);tag.on_join(room,player)
        room['round']=round_number-1
        tag.start(room,now=100,rng=random.Random(3))
        room.update(countdown=0,tagger=0,host_seen=100)
        for player in room['players']:player['live_seen']=100
        return room

    def tick(self,room,elapsed=None):
        if elapsed is not None:room['elapsed']=elapsed
        now=room['last_tick']+.05
        room['host_seen']=now
        for player in room['players']:player['live_seen']=now
        tag.advance(room,now,rng=random.Random(5))
        return now

    def test_progression_and_eight_safe_spawns(self):
        for number in range(1,15):
            room=self.room(number,8);field=room['arena']
            self.assertEqual(len(field['walls']),min(8,(number-1)*2))
            self.assertEqual(len(field['portals']),2 if number>=3 else 0)
            self.assertEqual(len(field['black_holes']),2 if number>=6 else 1 if number>=4 else 0)
            for portal in field['portals']:
                self.assertTrue(arena.free(portal['x'],portal['y']-65,field))
            for i,pos in enumerate(room['positions']):
                self.assertTrue(arena.safe_spot(pos['x'],pos['y'],field,room['positions'][:i]))
            tag.finish(room);tag.start(room,now=100,rng=random.Random(1))
            self.assertEqual(room['round'],number+1)

    def test_wall_stops_fast_movement_and_slides_in_both_axes(self):
        field=dict(walls=[dict(x=300,y=100,w=30,h=200)],portals=[],black_holes=[])
        pos=dict(x=250,y=140)
        arena.move(pos,200,60,field)
        self.assertAlmostEqual(pos['x'],280,places=2)
        self.assertAlmostEqual(pos['y'],200,places=2)
        self.assertFalse(arena.touches_wall(**pos,wall=field['walls'][0]))
        field['walls']=[dict(x=100,y=300,w=200,h=30)]
        pos=dict(x=140,y=250);arena.move(pos,60,200,field)
        self.assertAlmostEqual(pos['y'],280,places=2)
        self.assertAlmostEqual(pos['x'],200,places=2)

    def test_server_uses_wall_collisions(self):
        room=self.room(2);room['positions'][0]=dict(x=300,y=240)
        tag.set_input(room,0,dict(match=room['match'],x=1,y=0),100)
        self.tick(room)
        self.assertLessEqual(room['positions'][0]['x'],310.001)

    def test_teleport_bidirectional_cooldown_and_warp_marker(self):
        room=self.room(3)
        first,second=room['arena']['portals']
        room['positions'][1]=dict(x=first['x'],y=first['y'])
        self.tick(room)
        self.assertEqual(room['positions'][1],dict(x=second['x'],y=second['y']-65))
        self.assertEqual(room['warps'][1],1)
        room['positions'][1]=dict(x=second['x'],y=second['y'])
        self.tick(room)
        self.assertEqual(room['warps'][1],1)
        self.tick(room,2)
        self.assertEqual(room['positions'][1],dict(x=first['x'],y=first['y']-65))
        self.assertEqual(room['warps'][1],2)

    def test_black_hole_changes_role_once_even_with_heart(self):
        room=self.room(4)
        room['positions'][1]=dict(x=600,y=360)
        room['shields'][1]=10
        self.tick(room)
        self.assertEqual(room['tagger'],1)
        self.assertEqual(room['catches'],[0,0])
        self.assertEqual(room['tag_version'],1)
        self.tick(room)
        self.assertEqual(room['tag_version'],1)
        # A player who stayed in the hole cannot keep stealing the role.
        room['tagger']=0;self.tick(room)
        self.assertEqual(room['tagger'],0)
        room['positions'][1]=dict(x=600,y=310);self.tick(room)
        room['positions'][1]=dict(x=600,y=360);self.tick(room)
        self.assertEqual(room['tagger'],1)

    def test_simultaneous_black_holes_choose_one_stable_target(self):
        room=self.room(6,3)
        for i,hole in enumerate(room['arena']['black_holes'],1):
            room['positions'][i]=dict(x=hole['x'],y=hole['y'])
        self.tick(room)
        self.assertEqual(room['tagger'],1)
        self.assertEqual(room['tag_version'],1)
        self.tick(room)
        self.assertEqual(room['tagger'],1)

    def test_heart_spawn_safe_periodic_and_expiring(self):
        room=self.room(6,8)
        self.tick(room,11.9);self.assertEqual(room['hearts'],[])
        self.tick(room,12)
        heart=room['hearts'][0]
        self.assertTrue(arena.safe_spot(heart['x'],heart['y'],room['arena'],room['positions']))
        self.assertAlmostEqual(heart['expires']-room['elapsed'],9)
        self.tick(room,21.1);self.assertEqual(room['hearts'],[])
        self.tick(room,24.1);self.assertEqual(len(room['hearts']),1)
        self.assertNotEqual(room['hearts'][0]['id'],heart['id'])

    def test_heart_protects_before_contact_for_exactly_ten_game_seconds(self):
        room=self.room()
        room['positions']=[dict(x=500,y=300),dict(x=525,y=300)]
        room['hearts']=[dict(id=1,x=525,y=300,r=16,expires=9)]
        self.tick(room)
        self.assertEqual(room['hearts'],[])
        self.assertEqual(room['tagger'],0)
        self.assertAlmostEqual(room['shields'][1],10.05)
        self.tick(room,9.99);self.assertEqual(room['tagger'],0)
        self.tick(room,10.01);self.assertEqual(room['tagger'],1)
        self.assertEqual(room['catches'],[1,0])

    def test_heart_claimed_by_one_nearest_player(self):
        room=self.room(1,3)
        room['positions']=[dict(x=100,y=100),dict(x=500,y=300),dict(x=510,y=300)]
        room['hearts']=[dict(id=1,x=505,y=300,r=16,expires=9)]
        self.tick(room)
        self.assertEqual(sum(value>0 for value in room['shields']),1)
        self.assertGreater(room['shields'][1],0)

    def test_pause_freezes_bonus_and_portal_clocks(self):
        room=self.room(3);room['shields'][1]=10
        room['players'][1]['live_seen']=0
        before=copy.deepcopy(room)
        tag.advance(room,100.1)
        self.assertTrue(room['paused'])
        for key in ['elapsed','shields','portal_ready','next_heart','hearts','positions']:
            self.assertEqual(room[key],before[key])

    def test_json_restore_and_old_room_compatibility(self):
        room=self.room(6);room['shields'][1]=7
        restored=json.loads(json.dumps(room))
        self.tick(restored)
        self.assertEqual(restored['arena'],room['arena'])
        self.assertEqual(restored['shields'],room['shields'])
        for key in ['round','arena','hearts','next_heart','heart_serial','shields','portal_ready','black_inside','warps']:room.pop(key)
        self.tick(room)
        self.assertEqual(room['arena']['walls'],[])
        self.assertEqual(room['shields'],[0,0])

    def test_public_snapshot_has_objects_and_remaining_protection(self):
        room=self.room(6);room['elapsed']=4;room['shields'][1]=14
        with patch('app.tag.time.time',return_value=100):view=tag.view(room,1)
        self.assertEqual(view['round'],6)
        self.assertEqual(len(view['arena']['portals']),2)
        self.assertEqual(view['players'][1]['shield_seconds'],10)
        self.assertTrue(view['players'][1]['protected'])
        self.assertNotIn('private-',json.dumps(view))
        self.assertNotIn('portal_ready',view)
