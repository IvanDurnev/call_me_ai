"""Durable rooms shared between workers; bearer credentials never go into QR codes."""
import hashlib
import io
import json
import secrets
import socket
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request, url_for

from .durak import GameError
from . import durak, imposter, whos, tag, activity

games_bp = Blueprint('games', __name__)
ROOM_TTL = 24 * 3600
GAME_RULES = {'durak': durak, 'imposter': imposter, 'whos': whos, 'tag': tag, 'activity': activity}
HOST_ENDPOINTS = {'durak': 'games.table_page', 'imposter': 'games.imposter_table_page', 'whos': 'games.whos_table_page', 'tag': 'games.tag_table_page', 'activity': 'games.activity_table_page'}


@contextmanager
def database():
    path = Path(current_app.config.get('GAMES_DB_PATH') or Path(current_app.instance_path) / 'games.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS rooms (code TEXT PRIMARY KEY, data TEXT NOT NULL, touched REAL NOT NULL)')
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    finally:
        connection.close()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def credential(room, token=None):
    token = token if token is not None else request.headers.get('Authorization', '').removeprefix('Bearer ')
    hashed = digest(token)
    if secrets.compare_digest(hashed, room['host']):
        return 'host', None
    for i, player in enumerate(room['players']):
        if secrets.compare_digest(hashed, player['token']):
            return 'player', i
    raise GameError('Подключитесь к комнате заново.')


def load_room(connection, code):
    row = connection.execute('SELECT data, touched FROM rooms WHERE code = ?', (code,)).fetchone()
    if not row or row[1] < time.time() - ROOM_TTL:
        raise GameError('Комната не найдена или устарела. Создайте новую.')
    try:
        room = json.loads(row[0])
        if not isinstance(room, dict): raise ValueError()
        if room.get('game') == 'activity': activity.validate(room)
        return room
    except (ValueError, TypeError):
        raise GameError('Сохранение комнаты повреждено. Создайте новую комнату в игротеке; старая сохранена.')


def save_room(connection, code, room):
    connection.execute('UPDATE rooms SET data = ?, touched = ? WHERE code = ?',
                       (json.dumps(room, ensure_ascii=False), time.time(), code))


def join_url(code):
    base = current_app.config.get('GAMES_PUBLIC_URL') or request.host_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(('192.0.2.1', 80))
                address = probe.getsockname()[0]
            port = f':{parsed.port}' if parsed.port else ''
            base = f'{parsed.scheme}://{address}{port}'
        except OSError:
            pass
    return base.rstrip('/') + url_for('games.join_page', code=code)


def rules(room):
    return GAME_RULES.get(room.get('game'), durak)


def room_view(code, room, index=None):
    result = rules(room).view(room, index)
    result.update(code=code, join_url=join_url(code))
    return result


@games_bp.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@games_bp.errorhandler(GameError)
def game_error(error):
    return jsonify(error=str(error)), 400


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise GameError('Некорректный запрос.')
    return data


@games_bp.get('/games/durak/<code>')
def table_page(code):
    return render_template('durak.html', code=code.upper(), mode='host')


@games_bp.get('/games/join')
@games_bp.get('/games/join/<code>')
def join_page(code=''):
    code = (code or request.args.get('code', '')).strip().upper()
    if not code:
        return render_template('games_join.html')
    try:
        with database() as connection:
            room = load_room(connection, code)
    except GameError as error:
        return render_template('games_join.html', error=str(error), code=code), 400
    if request.args.get('code'):
        return redirect(url_for('games.join_page', code=code))
    template = room.get('game', 'durak') + '.html'
    return render_template(template, code=code, mode='player')


@games_bp.get('/games/imposter/<code>')
def imposter_table_page(code):
    return render_template('imposter.html', code=code.upper(), mode='host')


@games_bp.get('/games/whos/<code>')
def whos_table_page(code):
    return render_template('whos.html', code=code.upper(), mode='host')


@games_bp.get('/games/tag/<code>')
def tag_table_page(code):
    return render_template('tag.html', code=code.upper(), mode='host')


@games_bp.get('/games/activity/<code>')
def activity_table_page(code):
    return render_template('activity.html', code=code.upper(), mode='host')


@games_bp.post('/api/games/rooms')
def create_room():
    data = request.get_json(silent=True) if request.is_json else {}
    if (not isinstance(data, dict) or not isinstance(data.get('game', 'durak'), str)
            or data.get('game', 'durak') not in GAME_RULES):
        raise GameError('Неизвестная игра.')
    game = data.get('game', 'durak')
    with database() as connection:
        connection.execute('DELETE FROM rooms WHERE touched < ?', (time.time() - ROOM_TTL,))
        if connection.execute('SELECT COUNT(*) FROM rooms').fetchone()[0] >= 1000:
            raise GameError('Слишком много комнат. Попробуйте позже.')
        code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(6))
        while connection.execute('SELECT 1 FROM rooms WHERE code = ?', (code,)).fetchone():
            code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(6))
        token = secrets.token_urlsafe(32)
        room = GAME_RULES[game].new_room()
        room['host'] = digest(token)
        connection.execute('INSERT INTO rooms VALUES (?, ?, ?)', (code, json.dumps(room), time.time()))
    return jsonify(code=code, token=token, url=url_for(HOST_ENDPOINTS[game], code=code)), 201


@games_bp.post('/api/games/rooms/<code>/join')
def join_room(code):
    data = payload()
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 24 or any(ord(c) < 32 for c in name):
        raise GameError('Введите имя от 1 до 24 символов.')
    with database() as connection:
        room = load_room(connection, code)
        if room['phase'] != 'lobby':
            raise GameError('Партия уже началась. Дождитесь следующей игры.')
        capacity = getattr(rules(room), 'MAX_PLAYERS', 6)
        if len(room['players']) >= capacity:
            raise GameError(f'В комнате уже {capacity} игроков.')
        if any(p['name'].casefold() == name.casefold() for p in room['players']):
            raise GameError('Это имя уже занято. Выберите другое.')
        token = secrets.token_urlsafe(32)
        player = dict(name=name, token=digest(token), hand=[], seen=time.time())
        room['players'].append(player)
        if room.get('game') == 'tag':
            tag.on_join(room, player)
        elif room.get('game') == 'activity':
            room['settings'] = None
        room['revision'] += 1
        save_room(connection, code, room)
    return jsonify(token=token)


@games_bp.get('/api/games/rooms/<code>')
def state(code):
    with database() as connection:
        room = load_room(connection, code)
        _, index = credential(room)
        if room.get('game') == 'imposter':
            imposter.expire(room)
        elif room.get('game') == 'tag':
            tag.advance(room)
        elif room.get('game') == 'activity':
            activity.expire(room)
        if index is not None:
            room['players'][index]['seen'] = time.time()
        save_room(connection, code, room)
        result = room_view(code, room, index)
        now = time.time()
        for i, player in enumerate(result['players']):
            player['online'] = now - room['players'][i]['seen'] < 15
    return jsonify(result)


@games_bp.post('/api/games/rooms/<code>/action')
def action(code):
    data = payload()
    with database() as connection:
        room = load_room(connection, code)
        role, index = credential(room)
        if room.get('game') == 'imposter' and imposter.expire(room):
            save_room(connection, code, room)
        elif room.get('game') == 'tag' and tag.advance(room):
            save_room(connection, code, room)
        elif room.get('game') == 'activity' and activity.expire(room):
            save_room(connection, code, room)
        independent = (room.get('game') == 'imposter' and data.get('action') in ('ready', 'vote')
                       and data.get('phase') == room['phase'] and data.get('match') == room['match']
                       and data.get('ballot') == room['ballot'])
        independent = independent or (room.get('game') == 'whos' and data.get('action') == 'vote'
                       and data.get('phase') == room['phase'] and data.get('match') == room['match']
                       and data.get('round') == room['round'])
        independent = independent or (room.get('game') == 'activity' and data.get('action') == 'draw'
                       and data.get('match') == room['match'] and room.get('active')
                       and data.get('turn_id') == room['active']['id'])
        if data.get('revision') != room['revision'] and not independent:
            return jsonify(error='Стол изменился. Повторите ход.'), 409
        action_name = data.get('action')
        if action_name == 'start' and role == 'host':
            rules(room).start(room)
        elif action_name == 'reset' and role == 'host':
            lobby = rules(room).new_room()
            lobby.update(players=room['players'], host=room['host'], revision=room['revision'])
            if 'match' in room:
                lobby['match'] = room['match']
            for player in lobby['players']:
                player['hand'] = []
            room = lobby
        elif action_name == 'remove' and role == 'host' and room['phase'] == 'lobby':
            target = data.get('target')
            if type(target) is not int or not 0 <= target < len(room['players']):
                raise GameError('Игрок не найден.')
            room['players'].pop(target)
            if room.get('game') == 'activity': room['settings'] = None
        elif role == 'host' and room.get('game') in ('imposter', 'whos', 'tag', 'activity'):
            rules(room).host_act(room, data)
        elif role == 'player':
            rules(room).act(room, index, data)
        else:
            raise GameError('Это действие доступно только игрокам.')
        room['revision'] += 1
        save_room(connection, code, room)
    return jsonify(ok=True)


@games_bp.get('/api/games/rooms/<code>/qr.svg')
def qr(code):
    with database() as connection:
        load_room(connection, code)
    image = qrcode.make(join_url(code), image_factory=qrcode.image.svg.SvgPathFillImage, border=4)
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(buffer.getvalue(), mimetype='image/svg+xml')
