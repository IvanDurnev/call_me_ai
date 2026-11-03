"""Authoritative, JSON-serializable rules for a 36-card throw-in Durak game."""
import secrets

SUITS = 'SHDC'


class GameError(ValueError):
    pass


def new_room():
    return dict(phase='lobby', players=[], deck=[], trump=None, table=[],
                attacker=None, defender=None, limit=0, taking=False, passed=[],
                winners=[], loser=None, discard=0, message='Ждём игроков', revision=0)


def rank(card):
    return int(card[1:])


def beats(card, attack, trump):
    return (card[0] == attack[0] and rank(card) > rank(attack)) or (card[0] == trump and attack[0] != trump)


def active(room):
    return [i for i in range(len(room['players'])) if i not in room['winners']]


def next_player(room, index):
    return next((i for step in range(1, len(room['players']) + 1)
                 if (i := (index + step) % len(room['players'])) not in room['winners']), index)


def start(room):
    if room['phase'] not in ('lobby', 'finished'):
        raise GameError('Партия уже идёт.')
    if len(room['players']) < 2:
        raise GameError('Для игры нужны хотя бы два игрока.')
    deck = [s + str(r) for s in SUITS for r in range(6, 15)]
    secrets.SystemRandom().shuffle(deck)
    room.update(phase='playing', deck=deck, trump=deck[0], table=[], winners=[],
                loser=None, discard=0, taking=False, passed=[])
    for player in room['players']:
        player['hand'] = [deck.pop() for _ in range(6)]
    trumps = [(rank(c), i) for i, p in enumerate(room['players']) for c in p['hand'] if c[0] == room['trump'][0]]
    room['attacker'] = min(trumps)[1] if trumps else 0
    begin_round(room)


def begin_round(room):
    room['defender'] = next_player(room, room['attacker'])
    room['limit'] = min(6, len(room['players'][room['defender']]['hand']))
    room.update(table=[], taking=False, passed=[])
    room['message'] = f"Ходит {room['players'][room['attacker']]['name']}"


def finish_round(room):
    defender, attacker = room['defender'], room['attacker']
    taking = room['taking']
    cards = [c for pair in room['table'] for c in pair if c]
    if taking:
        room['players'][defender]['hand'].extend(cards)
    else:
        room['discard'] += len(cards)
    # Attacker first, clockwise, defender last; eliminated players never draw.
    order = [(attacker + step) % len(room['players']) for step in range(len(room['players']))]
    order = [i for i in order if i != defender and i not in room['winners']] + [defender]
    for i in order:
        hand = room['players'][i]['hand']
        while len(hand) < 6 and room['deck']:
            hand.append(room['deck'].pop())
    if not room['deck']:
        room['winners'].extend(i for i in active(room) if not room['players'][i]['hand'])
    remaining = active(room)
    room['table'] = []
    if len(remaining) <= 1:
        room.update(phase='finished', loser=remaining[0] if remaining else None,
                    message='Ничья!' if not remaining else f"Дурак — {room['players'][remaining[0]]['name']}")
        return
    room['attacker'] = next_player(room, defender) if taking or defender in room['winners'] else defender
    begin_round(room)


def act(room, player_index, payload):
    if room['phase'] != 'playing':
        raise GameError('Сейчас партия не идёт.')
    if player_index in room['winners']:
        raise GameError('Вы уже вышли из игры.')
    player = room['players'][player_index]
    action = payload.get('action')
    defender = room['defender']
    table = room['table']
    if action == 'attack':
        card = payload.get('card')
        if player_index == defender or (not table and player_index != room['attacker']):
            raise GameError('Сейчас не ваш ход.')
        if card not in player['hand']:
            raise GameError('Этой карты нет у вас в руке.')
        if len(table) >= room['limit']:
            raise GameError('Больше карт подкинуть нельзя.')
        if table and rank(card) not in {rank(c) for pair in table for c in pair if c}:
            raise GameError('Подкинуть можно только достоинство, которое есть на столе.')
        player['hand'].remove(card)
        table.append([card, None])
        room['passed'] = []
        room['message'] = 'Подкидывайте или завершите ход' if room['taking'] else f"Отбивается {room['players'][defender]['name']}"
    elif action == 'defend':
        card, target = payload.get('card'), payload.get('target')
        if player_index != defender or room['taking']:
            raise GameError('Сейчас вы не можете отбиваться.')
        if type(target) is not int or not 0 <= target < len(table) or table[target][1]:
            raise GameError('Выберите непокрытую карту на столе.')
        if card not in player['hand'] or not beats(card, table[target][0], room['trump'][0]):
            raise GameError('Эта карта не бьёт выбранную карту.')
        player['hand'].remove(card)
        table[target][1] = card
        room['message'] = 'Всё отбито. Подкидывайте или нажмите «Бито».' if all(p[1] for p in table) else 'Нужно отбить оставшиеся карты'
    elif action == 'take':
        if player_index != defender or not table or room['taking']:
            raise GameError('Сейчас нельзя забрать карты.')
        room['taking'] = True
        room['passed'] = []
        room['message'] = f"{player['name']} берёт. Остальные могут подкинуть или завершить ход."
    elif action == 'pass':
        if player_index == defender or not table or (not room['taking'] and any(not p[1] for p in table)):
            raise GameError('Сначала нужно отбить все карты или объявить «Беру».')
        if player_index not in room['passed']:
            room['passed'].append(player_index)
    else:
        raise GameError('Неизвестное действие.')
    if table and (room['taking'] or all(p[1] for p in table)):
        others = [i for i in active(room) if i != defender]
        if len(table) == room['limit'] or all(i in room['passed'] for i in others):
            finish_round(room)


def view(room, player_index=None):
    result = {k: v for k, v in room.items() if k not in ('deck', 'players', 'host')}
    result['deck_count'] = len(room['deck'])
    result['players'] = [dict(name=p['name'], count=len(p['hand']),
                              finished=i in room['winners']) for i, p in enumerate(room['players'])]
    result['you'] = player_index
    result['hand'] = list(room['players'][player_index]['hand']) if player_index is not None else []
    return result
