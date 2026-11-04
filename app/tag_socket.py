"""Authenticated realtime input and arena snapshots, shared across workers via SQLite."""
import json
import secrets
import time

from flask import current_app
from simple_websocket import ConnectionClosed

from . import tag
from .durak import GameError


def decode(raw):
    if not isinstance(raw, str) or len(raw) > 1024:
        raise GameError('Некорректное игровое сообщение.')
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise GameError('Некорректное игровое сообщение.')
    if not isinstance(data, dict):
        raise GameError('Некорректное игровое сообщение.')
    return data


def serve(ws, code):
    from .game_routes import database, load_room, save_room, credential, room_view
    socket_id = secrets.token_hex(16)
    index = None
    role = None
    match = None
    try:
        auth = decode(ws.receive(timeout=5))
        token = auth.get('token')
        if auth.get('type') != 'auth' or not isinstance(token, str) or len(token) > 128:
            raise GameError('Подключитесь к комнате заново.')
        with database() as connection:
            room = load_room(connection, code)
            if room.get('game') != 'tag' or room['phase'] != 'playing':
                raise GameError('Дождитесь начала раунда.')
            role, index = credential(room, token)
            match = room['match']
            if auth.get('match') != match:
                raise GameError('Состояние игры изменилось. Переподключаемся.')
            now = time.time()
            if role == 'host':
                room.update(host_socket=socket_id, host_seen=now)
            else:
                room['players'][index].update(socket_id=socket_id, live_seen=now, seen=now)
                room['controls'][index] = dict(x=0, y=0, at=now)
            save_room(connection, code, room)
        next_frame = time.monotonic()
        pending = None
        last_packet = time.time()
        window_start, packets = time.monotonic(), 0
        while True:
            raw = ws.receive(timeout=max(0, next_frame-time.monotonic()))
            if raw is not None:
                data = decode(raw)
                now_mono = time.monotonic()
                if now_mono-window_start >= 1:
                    window_start, packets = now_mono, 0
                packets += 1
                if packets > 90:
                    raise GameError('Слишком много команд. Переподключаемся.')
                if data.get('type') == 'input' and role == 'player':
                    tag.vector(data.get('x'), data.get('y'))
                    if data.get('match') != match:
                        raise GameError('Этот раунд уже завершён.')
                    pending = data
                elif data.get('type') != 'ping':
                    raise GameError('Недоступная игровая команда.')
                last_packet = time.time()
            if time.monotonic() < next_frame:
                continue
            now = time.time()
            if now-last_packet > 8:
                raise GameError('Связь прервалась. Переподключаемся.')
            with database() as connection:
                room = load_room(connection, code)
                if room.get('game') != 'tag':
                    raise GameError('Комната другой игры.')
                stopped = room['phase'] != 'playing' or room['match'] != match
                if stopped:
                    snapshot = room_view(code, room, index)
                else:
                    current_id = room.get('host_socket') if role == 'host' else room['players'][index].get('socket_id')
                    if current_id != socket_id:
                        raise GameError('Управление открыто на другом экране.')
                    # Advance with previous input before applying the newest direction.
                    tag.advance(room, now)
                    if role == 'host':
                        room['host_seen'] = last_packet
                    else:
                        room['players'][index]['live_seen'] = last_packet
                        room['players'][index]['seen'] = last_packet
                        if pending and room['phase'] == 'playing':
                            tag.set_input(room, index, pending, now)
                            pending = None
                    save_room(connection, code, room)
                    snapshot = tag.view(room, index)
            # Never hold the shared room database lock while sending to a slow peer.
            ws.send(json.dumps(dict(type='state', state=snapshot), ensure_ascii=False))
            if stopped or snapshot['phase'] != 'playing':
                break
            next_frame = time.monotonic()+tag.STEP
    except GameError as error:
        try:
            ws.send(json.dumps(dict(type='error', error=str(error)), ensure_ascii=False))
        except Exception:
            pass
    except ConnectionClosed:
        pass
    except Exception:
        current_app.logger.exception('Tag websocket failed')
    finally:
        if role is not None:
            try:
                with database() as connection:
                    room = load_room(connection, code)
                    if room.get('game') == 'tag' and room['phase'] == 'playing' and room['match'] == match:
                        if role == 'host' and room.get('host_socket') == socket_id:
                            room['host_seen'] = 0
                        elif role == 'player' and room['players'][index].get('socket_id') == socket_id:
                            room['players'][index]['live_seen'] = 0
                            room['controls'][index].update(x=0, y=0)
                        save_room(connection, code, room)
            except Exception:
                pass
        try:
            ws.close()
        except Exception:
            pass
