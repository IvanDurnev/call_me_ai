"""Who is most likely: anonymous nominations and a shared reveal after each question."""
import secrets

from .durak import GameError

MIN_PLAYERS = 3
MAX_PLAYERS = 12
ROUND_COUNT = 10
QUESTIONS = [
    'Кто из нас скорее заблудится даже с навигатором?',
    'Кто из нас лучше всех спланирует отпуск?',
    'Кто из нас скорее подружится с незнакомцем в очереди?',
    'Кто из нас может рассмешить всю компанию одним словом?',
    'Кто из нас скорее съест десерт до основного блюда?',
    'Кто из нас дольше всех будет выбирать фильм?',
    'Кто из нас скорее начнёт танцевать первым?',
    'Кто из нас помнит больше всего забавных семейных историй?',
    'Кто из нас скорее возьмёт в поездку лишний чемодан?',
    'Кто из нас лучше всех придумает имя для питомца?',
    'Кто из нас скорее принесёт домой бездомного котёнка?',
    'Кто из нас быстрее всех соберёт пазл?',
    'Кто из нас скорее забудет, зачем зашёл в комнату?',
    'Кто из нас умеет сделать праздник из обычного дня?',
    'Кто из нас скорее придумает новое правило настольной игры?',
    'Кто из нас будет фотографировать еду, пока она остывает?',
    'Кто из нас скорее выиграет конкурс смешных костюмов?',
    'Кто из нас лучше всех объяснит сложную вещь простыми словами?',
    'Кто из нас скорее заговорит с собакой на улице?',
    'Кто из нас первым заметит радугу?',
    'Кто из нас скорее устроит пикник прямо в гостиной?',
    'Кто из нас лучше всех соберёт команду для приключения?',
    'Кто из нас скорее уснёт во время фильма?',
    'Кто из нас знает больше всего песен наизусть?',
    'Кто из нас скорее испечёт торт без рецепта?',
    'Кто из нас дольше всех будет прощаться у двери?',
    'Кто из нас скорее превратит уборку в игру?',
    'Кто из нас лучше всех изобразит сказочного персонажа?',
    'Кто из нас скорее придумает необычный бутерброд?',
    'Кто из нас сможет весь день разговаривать цитатами из фильмов?',
    'Кто из нас скорее заведёт коллекцию необычных камней?',
    'Кто из нас лучше всех выберет подарок?',
    'Кто из нас скорее начнёт подпевать в магазине?',
    'Кто из нас быстрее всех разберётся в новой игре?',
    'Кто из нас скорее возьмёт зонтик в солнечный день?',
    'Кто из нас найдёт применение любой пустой коробке?',
    'Кто из нас скорее построит шалаш из одеял?',
    'Кто из нас лучше всех придумает историю на ходу?',
    'Кто из нас скорее отправит сообщение не в тот чат?',
    'Кто из нас способен часами наблюдать за животными?',
    'Кто из нас скорее устроит экскурсию по собственному дому?',
    'Кто из нас лучше всех помнит дни рождения?',
    'Кто из нас скорее придумает смешное название нашей компании?',
    'Кто из нас первым предложит сыграть ещё раз?',
    'Кто из нас скорее выберет самый необычный вкус мороженого?',
    'Кто из нас лучше всех найдёт потерявшуюся вещь?',
    'Кто из нас скорее отправится в спонтанную поездку?',
    'Кто из нас может сделать сто фотографий одного заката?',
    'Кто из нас скорее научит всех новому слову?',
    'Кто из нас лучше всех организует вечер настольных игр?',
    'Кто из нас скорее назовёт домашнее растение по имени?',
    'Кто из нас первым заметит, что кто-то поменял причёску?',
    'Кто из нас скорее придумает секретное рукопожатие?',
    'Кто из нас лучше всех украсит дом к празднику?',
    'Кто из нас скорее начнёт искать сокровища по старой карте?',
    'Кто из нас найдёт повод устроить чаепитие?',
    'Кто из нас скорее построит самого необычного снеговика?',
    'Кто из нас лучше всех изобразит голос робота?',
    'Кто из нас скорее придумает семейный девиз?',
    'Кто из нас сможет превратить любую прогулку в приключение?',
]


def new_room():
    return dict(game='whos', phase='lobby', players=[], revision=0, match=0,
                questions=[], round=0, votes={}, history=[], totals=[], nominations=[])


def start(room):
    if room['phase'] not in ('lobby', 'finished'):
        raise GameError('Партия уже идёт.')
    if not MIN_PLAYERS <= len(room['players']) <= MAX_PLAYERS:
        raise GameError('Для игры нужны от 3 до 12 игроков.')
    room.update(phase='voting', match=room.get('match', 0) + 1,
                questions=secrets.SystemRandom().sample(QUESTIONS, ROUND_COUNT),
                round=1, votes={}, history=[], totals=[0]*len(room['players']),
                nominations=[0]*len(room['players']))


def reveal(room):
    counts = [0]*len(room['players'])
    for target in room['votes'].values():
        if target is not None:
            counts[target] += 1
    highest = max(counts)
    leaders = [i for i, count in enumerate(counts) if highest and count == highest]
    for i, count in enumerate(counts):
        room['totals'][i] += count
        if i in leaders:
            room['nominations'][i] += 1
    room['history'].append(dict(question=room['questions'][room['round']-1],
                                counts=counts, leaders=leaders,
                                skipped=len(room['players'])-sum(counts)))
    room['phase'] = 'results'


def act(room, index, data):
    if room['phase'] != 'voting' or data.get('action') != 'vote':
        raise GameError('Сейчас голосование закрыто.')
    if str(index) in room['votes']:
        raise GameError('Ваш ответ уже принят.')
    if 'target' not in data:
        raise GameError('Выберите игрока или пропустите вопрос.')
    target = data['target']
    if target is not None and (type(target) is not int or not 0 <= target < len(room['players'])):
        raise GameError('Игрок не найден.')
    room['votes'][str(index)] = target
    if len(room['votes']) == len(room['players']):
        reveal(room)


def host_act(room, data):
    action = data.get('action')
    if action == 'reveal' and room['phase'] == 'voting':
        if not room['votes']:
            raise GameError('Дождитесь хотя бы одного ответа.')
        reveal(room)
    elif action == 'next' and room['phase'] == 'results':
        if room['round'] == ROUND_COUNT:
            room['phase'] = 'finished'
        else:
            room.update(phase='voting', round=room['round']+1, votes={})
    else:
        raise GameError('Сейчас это действие недоступно.')


def view(room, index=None):
    result = {key: room[key] for key in ('game', 'phase', 'revision', 'match', 'round')}
    # Current choices and cumulative counts stay private until the question closes.
    revealed = room['phase'] in ('results', 'finished')
    previous = room['history'][-1] if revealed else None
    result.update(you=index, round_count=ROUND_COUNT,
                  question=room['questions'][room['round']-1] if room['round'] else None,
                  players=[dict(name=p['name'], voted=str(i) in room['votes']) for i, p in enumerate(room['players'])],
                  vote_count=len(room['votes']), has_voted=str(index) in room['votes'] if index is not None else False,
                  own_vote=room['votes'].get(str(index)) if index is not None else None,
                  result=previous, totals=list(room['totals']) if revealed else [],
                  nominations=list(room['nominations']) if revealed else [],
                  history=room['history'] if room['phase'] == 'finished' else [])
    return result
