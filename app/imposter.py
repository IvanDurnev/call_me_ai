"""Family word game: private roles, ordered clues, secret ballots and a final guess."""
import re
import secrets
import time

from .durak import GameError

MIN_PLAYERS = 3
MAX_PLAYERS = 12
WORDS = {
    'Еда': ['Пицца', 'Мороженое', 'Арбуз', 'Шоколад', 'Блины', 'Попкорн', 'Лимон', 'Сыр', 'Мёд', 'Огурец', 'Пельмени', 'Торт', 'Банан', 'Клубника', 'Морковь', 'Макароны', 'Суп', 'Хлеб', 'Чай', 'Сок'],
    'Животные': ['Пингвин', 'Жираф', 'Дельфин', 'Кенгуру', 'Крокодил', 'Белка', 'Ёж', 'Слон', 'Черепаха', 'Осьминог', 'Лев', 'Панда', 'Лиса', 'Бабочка', 'Сова', 'Верблюд', 'Лягушка', 'Кит', 'Зебра', 'Хомяк'],
    'Места': ['Аэропорт', 'Библиотека', 'Цирк', 'Пляж', 'Зоопарк', 'Музей', 'Стадион', 'Школа', 'Кинотеатр', 'Бассейн', 'Парк', 'Магазин', 'Вокзал', 'Кухня', 'Балкон', 'Театр', 'Кафе', 'Лифт', 'Космос', 'Деревня'],
    'Предметы': ['Зонт', 'Велосипед', 'Подушка', 'Чемодан', 'Гитара', 'Зеркало', 'Холодильник', 'Телескоп', 'Рюкзак', 'Будильник', 'Карандаш', 'Ложка', 'Мяч', 'Робот', 'Ключ', 'Фонарик', 'Книга', 'Шляпа', 'Пылесос', 'Самокат'],
    'Природа': ['Вулкан', 'Радуга', 'Водопад', 'Пустыня', 'Айсберг', 'Снежинка', 'Молния', 'Океан', 'Облако', 'Луна', 'Река', 'Гора', 'Лес', 'Остров', 'Солнце', 'Ветер', 'Туман', 'Пещера', 'Ракушка', 'Сосулька'],
}


def normalize(text):
    return ''.join(c for c in text.casefold().replace('ё', 'е') if c.isalpha())


def new_room():
    return dict(game='imposter', phase='lobby', players=[], revision=0, match=0,
                category=None, word=None, imposter=None, order=[], ready=[], clues=[],
                round=0, turn=0, votes={}, ballot=0, candidates=[], tally={},
                suspect=None, deadline=None, winner=None, reason=None, guess=None)


def start(room):
    if room['phase'] not in ('lobby', 'finished'):
        raise GameError('Раунд уже идёт.')
    if not MIN_PLAYERS <= len(room['players']) <= MAX_PLAYERS:
        raise GameError('Для игры нужны от 3 до 12 игроков.')
    category = secrets.choice(list(WORDS))
    choices = [word for word in WORDS[category] if word != room.get('word')]
    order = list(range(len(room['players'])))
    secrets.SystemRandom().shuffle(order)
    room.update(phase='reveal', match=room.get('match', 0) + 1,
                category=category, word=secrets.choice(choices),
                imposter=secrets.randbelow(len(order)), order=order, ready=[], clues=[],
                round=1, turn=0, votes={}, ballot=0, candidates=[], tally={},
                suspect=None, deadline=None, winner=None, reason=None, guess=None)


def finish(room, winner, reason):
    room.update(phase='finished', winner=winner, reason=reason, deadline=None)


def expire(room):
    if room['phase'] == 'guess' and time.time() >= room['deadline']:
        finish(room, 'team', 'Импостер не успел угадать слово.')
        room['revision'] += 1
        return True
    return False


def begin_voting(room):
    room.update(phase='voting', votes={}, ballot=room['ballot'] + 1,
                candidates=list(range(len(room['players']))))


def resolve_votes(room):
    tally = {str(i): 0 for i in room['candidates']}
    for choice in room['votes'].values():
        tally[str(choice)] += 1
    room['tally'] = tally
    most = max(tally.values())
    leaders = [int(i) for i, count in tally.items() if count == most]
    if len(leaders) > 1:
        if room['ballot'] == 1:
            room.update(votes={}, ballot=2, candidates=leaders)
        else:
            finish(room, 'imposter', 'Повторное голосование завершилось ничьей. Импостер остался нераскрытым.')
    else:
        room['suspect'] = leaders[0]
        if leaders[0] == room['imposter']:
            room.update(phase='guess', deadline=time.time() + 30)
        else:
            finish(room, 'imposter', 'Компания выбрала мирного игрока. Импостер остался нераскрытым.')


def add_clue(room, text):
    index = room['order'][room['turn']]
    room['clues'].append(dict(player=index, text=text, round=room['round']))
    room['turn'] += 1
    if room['turn'] == len(room['order']):
        room['phase'] = 'discussion'


def host_act(room, data):
    action = data.get('action')
    if action == 'voting' and room['phase'] == 'discussion':
        begin_voting(room)
    elif action == 'another_round' and room['phase'] == 'discussion' and room['round'] < 3:
        room.update(phase='clues', round=room['round'] + 1, turn=0)
    elif action == 'skip' and room['phase'] == 'clues':
        add_clue(room, 'Пропуск')
    else:
        raise GameError('Сейчас это действие недоступно.')


def act(room, index, data):
    action = data.get('action')
    if action == 'ready' and room['phase'] == 'reveal':
        if index not in room['ready']:
            room['ready'].append(index)
        if len(room['ready']) == len(room['players']):
            room['phase'] = 'clues'
    elif action == 'clue' and room['phase'] == 'clues':
        if index != room['order'][room['turn']]:
            raise GameError('Дождитесь своей очереди.')
        text = data.get('text')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 32 or not re.fullmatch(r'[^\W\d_]+(?:[- ][^\W\d_]+){0,2}', text.strip(), re.UNICODE):
            raise GameError('Напишите ассоциацию: от одного до трёх слов, до 32 символов.')
        if normalize(text) == normalize(room['word']) or normalize(room['word']) in [normalize(w) for w in text.split()]:
            raise GameError('Секретное слово нельзя называть напрямую.')
        add_clue(room, text.strip())
    elif action == 'vote' and room['phase'] == 'voting':
        target = data.get('target')
        if type(target) is not int or target not in room['candidates'] or target == index:
            raise GameError('Выберите другого игрока из списка кандидатов.')
        if str(index) in room['votes']:
            raise GameError('Ваш голос уже принят.')
        room['votes'][str(index)] = target
        if len(room['votes']) == len(room['players']):
            resolve_votes(room)
    elif action == 'guess' and room['phase'] == 'guess':
        if index != room['imposter']:
            raise GameError('Угадать слово может только импостер.')
        text = data.get('text')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 64 or not normalize(text):
            raise GameError('Введите слово, которое хотите угадать.')
        room['guess'] = text.strip()
        correct = normalize(text) == normalize(room['word'])
        finish(room, 'imposter' if correct else 'team', 'Импостер угадал секретное слово!' if correct else 'Импостер не угадал секретное слово.')
    else:
        raise GameError('Сейчас это действие недоступно.')


def view(room, index=None):
    # An allowlist prevents role, word and individual ballot leaks on the shared screen.
    result = {key: room[key] for key in ('game', 'phase', 'revision', 'match', 'category', 'order',
              'clues', 'round', 'turn', 'ballot', 'candidates', 'suspect', 'deadline', 'winner', 'reason')}
    result.update(you=index, players=[dict(name=p['name'], ready=i in room['ready'],
                  voted=str(i) in room['votes']) for i, p in enumerate(room['players'])],
                  ready_count=len(room['ready']), vote_count=len(room['votes']),
                  own_vote=room['votes'].get(str(index)) if index is not None else None,
                  server_time=time.time())
    if room['phase'] != 'lobby' and index is not None:
        result['role'] = 'imposter' if index == room['imposter'] else 'team'
        if index != room['imposter']:
            result['word'] = room['word']
    if room['phase'] in ('guess', 'finished'):
        result['imposter'] = room['imposter']
        result['tally'] = room['tally']
    if room['phase'] == 'finished':
        result.update(word=room['word'], guess=room['guess'])
    return result
