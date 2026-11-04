"""Server-authoritative two-minute tag arena. Coordinates are never accepted from clients."""
import math
import secrets
import time

from .durak import GameError

MIN_PLAYERS = 2
MAX_PLAYERS = 8
WIDTH, HEIGHT = 1200, 720
RADIUS = 20
SPEED = 225
DURATION = 120
STEP = 1 / 20
INPUT_TTL = 0.3
CONNECTION_TTL = 1.8
COLORS = ['#e86c60', '#448ed0', '#e7b43d', '#8c6ed0', '#48a984', '#df75ac', '#e58c41', '#46a8b5']


def new_room():
    return dict(game='tag', phase='lobby', players=[], revision=0, match=0,
                tagger=None, positions=[], controls=[], elapsed=0, countdown=3,
                last_tick=0, host_seen=0, host_socket=None, tag_lock_until=0,
                immunity=[], tag_seconds=[], catches=[], tag_version=0,
                paused=True, pause_reason='Подключаем телефоны', winners=[])


def on_join(room, player):
    used = {p.get('color') for p in room['players'] if p is not player}
    player['color'] = next(color for color in COLORS if color not in used)


def start(room):
    if room['phase'] not in ('lobby', 'finished'):
        raise GameError('Раунд уже идёт.')
    count = len(room['players'])
    if not MIN_PLAYERS <= count <= MAX_PLAYERS:
        raise GameError('Для игры нужны от 2 до 8 игроков.')
    room.update(phase='playing', match=room.get('match', 0)+1,
                tagger=secrets.randbelow(count),
                positions=[dict(x=WIDTH/2 + 260*math.cos(i*2*math.pi/count),
                                y=HEIGHT/2 + 210*math.sin(i*2*math.pi/count)) for i in range(count)],
                controls=[dict(x=0, y=0, at=0) for _ in range(count)],
                elapsed=0, countdown=3, last_tick=time.time(), host_seen=0, host_socket=None,
                tag_lock_until=0, immunity=[0]*count, tag_seconds=[0]*count,
                catches=[0]*count, tag_version=0, paused=True,
                pause_reason='Ждём подключения всех игроков и общего экрана', winners=[])
    for player in room['players']:
        player.update(live_seen=0, socket_id=None)


def vector(x, y):
    try:
        valid = type(x) in (int, float) and type(y) in (int, float) and math.isfinite(x) and math.isfinite(y)
    except OverflowError:
        valid = False
    if not valid:
        raise GameError('Некорректное направление движения.')
    length = math.hypot(x, y)
    scale = max(1, length)
    return x/scale, y/scale


def set_input(room, index, data, now):
    if room['phase'] != 'playing' or data.get('match') != room['match']:
        raise GameError('Этот раунд уже завершён.')
    x, y = vector(data.get('x'), data.get('y'))
    room['controls'][index] = dict(x=x, y=y, at=now)
    room['players'][index]['live_seen'] = now
    room['players'][index]['seen'] = now


def finish(room):
    room['phase'] = 'finished'
    minimum = min(round(value, 1) for value in room['tag_seconds'])
    room['winners'] = [i for i, value in enumerate(room['tag_seconds']) if round(value, 1) == minimum]
    for control in room['controls']:
        control.update(x=0, y=0)
    room['revision'] += 1


def advance(room, now=None):
    now = time.time() if now is None else now
    if room['phase'] != 'playing':
        return False
    delta = now-room['last_tick']
    if delta < STEP - 0.002:
        return False
    room['last_tick'] = now
    # A stalled worker, reconnect or clock jump must never teleport a character.
    dt = min(delta, 0.1)
    missing = [p['name'] for p in room['players'] if now-p.get('live_seen', 0) > CONNECTION_TTL]
    host_missing = now-room['host_seen'] > CONNECTION_TTL
    room['paused'] = bool(missing or host_missing)
    room['pause_reason'] = ('Ждём общий экран' if host_missing else 'Ждём: '+', '.join(missing)) if room['paused'] else ''
    if room['paused']:
        return True
    if room['countdown'] > 0:
        room['countdown'] = max(0, room['countdown']-dt)
        return True
    dt = min(dt, DURATION-room['elapsed'])
    room['tag_seconds'][room['tagger']] += dt
    room['elapsed'] += dt
    for i, position in enumerate(room['positions']):
        control = room['controls'][i]
        if now-control['at'] > INPUT_TTL:
            continue
        # A small speed advantage keeps the chase moving even in an open arena.
        speed = SPEED * (1.07 if i == room['tagger'] else 1)
        position['x'] = max(40, min(WIDTH-40, position['x']+control['x']*speed*dt))
        position['y'] = max(40, min(HEIGHT-70, position['y']+control['y']*speed*dt))
    if room['elapsed'] >= room['tag_lock_until']:
        old = room['tagger']
        pos = room['positions'][old]
        targets = [(math.hypot(other['x']-pos['x'], other['y']-pos['y']), i)
                   for i, other in enumerate(room['positions'])
                   if i != old and room['immunity'][i] <= room['elapsed']]
        if targets:
            distance, target = min(targets)
            if distance <= 2*RADIUS:
                room['tagger'] = target
                room['catches'][old] += 1
                room['tag_lock_until'] = room['elapsed']+1.2
                room['immunity'][old] = room['elapsed']+2.2
                room['tag_version'] += 1
    if room['elapsed'] >= DURATION:
        finish(room)
    return True


def act(room, index, data):
    raise GameError('Управление движением доступно через игровое соединение.')


def host_act(room, data):
    if data.get('action') == 'stop' and room['phase'] == 'playing':
        finish(room)
    else:
        raise GameError('Сейчас это действие недоступно.')


def view(room, index=None):
    now = time.time()
    active = room['phase'] != 'lobby'
    result = {key: room[key] for key in ('game', 'phase', 'revision', 'match', 'tagger', 'elapsed',
              'countdown', 'paused', 'pause_reason', 'tag_version', 'winners')}
    result.update(you=index, width=WIDTH, height=HEIGHT, radius=RADIUS,
                  duration=DURATION, server_time=now,
                  players=[dict(name=p['name'], color=p['color'],
                      x=round(room['positions'][i]['x'], 2) if active else None,
                      y=round(room['positions'][i]['y'], 2) if active else None,
                      connected=now-p.get('live_seen', 0) <= CONNECTION_TTL,
                      seconds=round(room['tag_seconds'][i], 1) if active else 0,
                      catches=room['catches'][i] if active else 0,
                      protected=active and room['immunity'][i] > room['elapsed'])
                      for i, p in enumerate(room['players'])])
    return result
