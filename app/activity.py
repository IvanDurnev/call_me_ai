"""Show & Explain: persistent decks, private presenter tasks and atomic board moves."""
import copy
import math
import secrets
import time

from .durak import GameError
from .activity_data import CARDS, CARD_BY_ID, CATEGORIES, CATEGORY_BY_KEY, DECK_IDS
from .activity_types import GameState, MoveTransaction, TurnResult

MIN_PLAYERS, MAX_PLAYERS = 2, 4
START, FINISH = 0, 49
RULESET = 'show-explain-team-phone-v2'
SCHEMA = 2
RNG = secrets.SystemRandom()
PHASES = {'lobby','handoff','choose_difficulty','choose_task','prepare','performing','board','finished'}
CHECKPOINT_FIELDS = ['settings','positions','cursor','offsets','decks','turn_number','phase','active','deadline','memorize_deadline','drawing','history','winners','last_transaction']
COLORS = ['#527fc3','#d4816c','#6f9c64','#a17bb8']
SYMBOLS = ['●','▲','■','◆']


def new_drawing():
    return dict(strokes=[], removed=[], seen=[], revision=0)


def new_room() -> GameState:
    return dict(game='activity', schemaVersion=SCHEMA, ruleset=RULESET, locale='ru',
                phase='lobby', players=[], revision=0, match=0, settings=None,
                positions=[], cursor=0, offsets=[], decks={}, turn_number=1,
                active=None, deadline=None, memorize_deadline=None, drawing=new_drawing(),
                history=[], winners=[], last_transaction=None, checkpoint=None, undo=None)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_settings(settings, players):
    if not isinstance(settings,dict): raise GameError('Проверьте настройки команд.')
    if settings.get('mode','teams')!='teams': raise GameError('Играем только командами: один телефон на команду.')
    if not 2<=len(players)<=MAX_PLAYERS: raise GameError('Подключите от 2 до 4 команд, по одному телефону на команду.')
    sizes=settings.get('sizes',[2]*len(players))
    if not isinstance(sizes,list) or len(sizes)!=len(players) or any(not integer(n,2,99) for n in sizes):
        raise GameError('В каждой команде должно быть минимум двое игроков.')
    starting=settings.get('starting_side',0)
    if not integer(starting,0,len(players)-1): raise GameError('Выберите команду, которая начнёт игру.')
    teams=[dict(name=p['name'],members=[i],first_presenter=i,size=sizes[i]) for i,p in enumerate(players)]
    return dict(mode='teams',teams=teams,sizes=list(sizes),starting_side=starting)


def validate(room):
    try:
        if room['schemaVersion']!=SCHEMA or room['ruleset']!=RULESET or room['phase'] not in PHASES: raise ValueError()
        if not isinstance(room['players'],list) or len(room['players'])>MAX_PLAYERS or any(not isinstance(p,dict) or not isinstance(p.get('name'),str) for p in room['players']): raise ValueError()
        if room['phase']=='lobby':
            if room['settings'] is not None: validate_settings(room['settings'],room['players'])
            return
        settings=validate_settings(room['settings'],room['players'])
        if {k:v for k,v in room['settings'].items() if k!='precision'}!=settings: raise ValueError()
        count=len(settings['teams'])
        if len(room['positions'])!=count or any(not integer(p,START,FINISH) for p in room['positions']): raise ValueError()
        if not integer(room['cursor'],0,count-1) or len(room['offsets'])!=count: raise ValueError()
        if any(not integer(offset,0,len(settings['teams'][i]['members'])-1) for i,offset in enumerate(room['offsets'])): raise ValueError()
        active=room['active']
        if not isinstance(active,dict) or not integer(active['presenter'],0,len(room['players'])-1) or not integer(active['side'],0,count-1): raise ValueError()
        if active['presenter'] not in settings['teams'][active['side']]['members']: raise ValueError()
        if active['side']!=room['cursor'] and room['phase'] not in ('board','finished'): raise ValueError()
        if active.get('category') is not None and active['category'] not in CATEGORY_BY_KEY: raise ValueError()
        if room['phase'] in ('prepare','performing') and active.get('category') not in allowed_categories(room['positions'][active['side']]): raise ValueError()
        if active.get('card') is not None and (active['card'] not in CARD_BY_ID or CARD_BY_ID[active['card']]['difficulty']!=active.get('difficulty')): raise ValueError()
        if room['phase'] in ('prepare','performing','board','finished') and (not active.get('card') or active.get('category') not in CATEGORY_BY_KEY): raise ValueError()
        held=active.get('card') if room['phase'] not in ('board','finished') else None
        for d,ids in DECK_IDS.items():
            deck=room['decks'][d]
            all_ids=deck['draw']+deck['discard']+([held] if held and CARD_BY_ID[held]['difficulty']==int(d) else [])
            if sorted(all_ids)!=sorted(ids): raise ValueError()
        clock_key = 'deadline' if room['phase']=='performing' else None
        if clock_key and (type(room[clock_key]) not in (int,float) or not math.isfinite(room[clock_key])): raise ValueError()
        if any(not integer(i,0,count-1) for i in room['winners']): raise ValueError()
        if not isinstance(room['drawing']['strokes'],list) or not isinstance(room['history'],list): raise ValueError()
    except (KeyError,TypeError,ValueError,IndexError,GameError):
        raise GameError('Сохранение игры повреждено или несовместимо. Создайте новую комнату в игротеке; старая сохранена.')


def snapshot(room):
    return copy.deepcopy({key:room[key] for key in CHECKPOINT_FIELDS})


def begin_turn(room, rng=None):
    rng=rng or RNG
    side=room['cursor'];members=room['settings']['teams'][side]['members']
    presenter=members[room['offsets'][side]]
    room.update(phase='handoff', deadline=None, memorize_deadline=None, drawing=new_drawing(),
                active=dict(id=f"{room['match']}-{room['turn_number']}-{rng.getrandbits(64):016x}",
                side=side,presenter=presenter,card=None,category=None,difficulty=None,open=False,
                draft=None,expired=False,gave_up=False))
    room['checkpoint']=snapshot(room)


def start(room, rng=None, now=None):
    if room['phase'] not in ('lobby','finished'): raise GameError('Партия уже идёт. Сначала вернитесь в лобби.')
    settings=validate_settings(room['settings'] or {},room['players'])
    rng=rng or RNG
    decks={}
    for d,ids in DECK_IDS.items():
        draw=list(ids);rng.shuffle(draw);decks[d]=dict(draw=draw,discard=[])
    offsets=[team['members'].index(team['first_presenter']) for team in settings['teams']]
    room.update(settings=settings,positions=[START]*len(settings['teams']),cursor=settings['starting_side'],
                offsets=offsets,decks=decks,turn_number=1,history=[],winners=[],undo=None,
                last_transaction=None,match=room.get('match',0)+1)
    begin_turn(room,rng)


def cell_category(position):
    return None if position in (START,FINISH) else CATEGORIES[(position-1)%6]['key']


def allowed_categories(position):
    if position==START: return [c['key'] for c in CATEGORIES]
    cell=cell_category(position)
    if cell is None: return []
    method=CATEGORY_BY_KEY[cell]['method']
    return [c['key'] for c in CATEGORIES if c['method']==method]


def select_task(card_id, position, category=None):
    if category not in allowed_categories(position):
        raise GameError('Выберите задание того же типа, что и клетка команды.')
    return category, copy.deepcopy(CARD_BY_ID[card_id]['tasks'][category])


def draw_card(room,difficulty,rng=None):
    if not integer(difficulty,3,5): raise GameError('Выберите сложность: 3, 4 или 5.')
    deck=room['decks'][str(difficulty)]
    if not deck['draw']:
        deck['draw']=list(deck['discard']);deck['discard']=[];(rng or RNG).shuffle(deck['draw'])
    if not deck['draw']: raise GameError('В колоде нет карточек.')
    card=deck['draw'].pop();room['active'].update(card=card,difficulty=difficulty)
    room['phase']='choose_task'


def choose_task(room,category):
    chosen,task=select_task(room['active']['card'],room['positions'][room['active']['side']],category)
    room['active'].update(category=chosen,open=False)
    room['phase']='prepare'


def expire(room,now=None):
    now=time.time() if now is None else now
    if room['phase']=='performing' and now>=room['deadline']:
        room['active'].update(expired=True,draft=dict(outcome='failure',guesser=None))
        apply_result(room)
        room['revision']+=1
        return True
    return False


def build_move_transaction(before,advances,turn_id) -> MoveTransaction:
    # Teams can share a cell. Only the successful team advances.
    after=list(before)
    for index,amount in advances.items(): after[index]=min(FINISH,before[index]+amount)
    return dict(turn_id=turn_id,before=list(before),after=after,advances=dict(advances),
                raw_destinations={i:before[i]+amount for i,amount in advances.items()},
                bumped=[],winners=[i for i,p in enumerate(after) if p>=FINISH])


def resolve_turn(room,result:TurnResult) -> MoveTransaction:
    active=room['active'];outcome=result.get('outcome')
    if outcome not in ('success','failure'): raise GameError('Неизвестный исход попытки.')
    if result.get('guesser') is not None: raise GameError('Угадывает своя команда; выбирать игрока не нужно.')
    advances={active['side']:active['difficulty']} if outcome=='success' else {}
    return build_move_transaction(room['positions'],advances,active['id'])


def apply_result(room):
    if room['phase'] not in ('prepare','performing'): raise GameError('Попытка уже завершена.')
    active=room['active'];draft=active.get('draft')
    if not draft: raise GameError('Сначала выберите и проверьте результат.')
    transaction=resolve_turn(room,draft)
    room['undo']=copy.deepcopy(room['checkpoint'])
    room['positions']=list(transaction['after'])
    room['decks'][str(active['difficulty'])]['discard'].append(active['card'])
    task=CARD_BY_ID[active['card']]['tasks'][active['category']]
    event=dict(turn_id=active['id'],number=room['turn_number'],presenter=active['presenter'],side=active['side'],
               difficulty=active['difficulty'],category=active['category'],open=active['open'],
               task=task['text'],outcome=draft['outcome'],guesser=draft.get('guesser'),transaction=transaction)
    room['history'].append(event);room['last_transaction']=transaction
    side=active['side'];room['offsets'][side]=(room['offsets'][side]+1)%len(room['settings']['teams'][side]['members'])
    room['cursor']=(side+1)%len(room['settings']['teams']);room['turn_number']+=1
    room.update(winners=transaction['winners'],phase='finished' if transaction['winners'] else 'board',
                deadline=None,memorize_deadline=None)


def undo_last(room,rng=None):
    if room['undo'] is None: raise GameError('Нет подтверждённого хода для отмены.')
    saved=copy.deepcopy(room['undo'])
    for key,value in saved.items(): room[key]=value
    room['undo']=None
    room['active']['id']=f"{room['match']}-{room['turn_number']}-{(rng or RNG).getrandbits(64):016x}"
    room['checkpoint']=snapshot(room)


def presenter_action(room,index,data,now=None,rng=None):
    now=time.time() if now is None else now
    if not room['active'] or index!=room['active']['presenter']: raise GameError('Сейчас ход другой команды.')
    action=data.get('action');phase=room['phase']
    if action=='take_turn' and phase=='handoff': room['phase']='choose_difficulty'
    elif action=='difficulty' and phase=='choose_difficulty': draw_card(room,data.get('difficulty'),rng)
    elif action=='task' and phase=='choose_task': choose_task(room,data.get('category'))
    elif action=='perform' and phase=='prepare': room.update(phase='performing',deadline=now+60)
    elif action=='guessed' and phase=='performing':
        if data.get('turn_id')!=room['active']['id'] or data.get('match')!=room['match']:
            raise GameError('Эта попытка уже завершена.')
        if now>=room['deadline']: raise GameError('Время вышло. В этой попытке команда не продвигается.')
        room['active']['draft']=dict(outcome='success',guesser=None)
        apply_result(room)
    elif action=='give_up' and phase in ('prepare','performing'):
        room['active'].update(gave_up=True,draft=dict(outcome='failure',guesser=None))
        apply_result(room)
    else: raise GameError('Сейчас это действие недоступно.')


def act(room,index,data,now=None,rng=None):
    validate(room)
    expire(room,now)
    if data.get('action')=='draw': draw_action(room,index,data)
    else: presenter_action(room,index,data,now,rng)


def host_act(room,data,now=None,rng=None):
    validate(room);expire(room,now)
    action=data.get('action');phase=room['phase']
    if action in ('configure','launch') and phase=='lobby':
        room['settings']=validate_settings(data.get('settings'),room['players'])
        if action=='launch':start(room,rng,now)
    elif action=='next' and phase=='board': begin_turn(room,rng)
    elif action=='undo' and room['undo'] is not None: undo_last(room,rng)
    else: raise GameError('Сейчас это действие недоступно.')


def draw_action(room,index,data):
    active=room['active']
    if (room['phase']!='performing' or index!=active['presenter']
            or CATEGORY_BY_KEY[active['category']]['method']!='draw'
            or data.get('turn_id')!=active['id'] or data.get('match')!=room['match']):
        raise GameError('Рисовать может ведущий во время своего задания.')
    op_id=data.get('op_id');drawing=room['drawing']
    if not isinstance(op_id,str) or not 1<=len(op_id)<=80: raise GameError('Некорректная команда рисунка.')
    if op_id in drawing['seen']: return
    kind=data.get('kind')
    if kind in ('clear','undo'):
        removed=drawing['strokes'] if kind=='clear' else drawing['strokes'][-1:]
        drawing['removed'].extend(stroke['id'] for stroke in removed)
        drawing['strokes']=[] if kind=='clear' else drawing['strokes'][:-1]
    elif kind=='stroke':
        stroke_id=data.get('stroke_id');points=data.get('points');seq=data.get('seq')
        if not isinstance(stroke_id,str) or not 1<=len(stroke_id)<=64 or not integer(seq,0,10000): raise GameError('Некорректный штрих.')
        if stroke_id in drawing['removed']: return
        if not isinstance(points,list) or not 1<=len(points)<=128: raise GameError('Слишком большой штрих.')
        cleaned=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2 or any(type(v) not in (int,float) or not 0<=v<=1 for v in point): raise GameError('Некорректные координаты рисунка.')
            cleaned.append([round(v,4) for v in point])
        stroke=next((s for s in drawing['strokes'] if s['id']==stroke_id),None)
        if stroke is None:
            if seq!=0 or data.get('tool') not in ('pen','eraser') or data.get('width') not in (3,7,14): raise GameError('Некорректное начало штриха.')
            if len(drawing['strokes'])>=300: raise GameError('Холст заполнен. Очистите его или используйте бумагу.')
            stroke=dict(id=stroke_id,tool=data['tool'],width=data['width'],points=[],seq=-1)
            drawing['strokes'].append(stroke)
        if seq<=stroke['seq']: return
        if seq!=stroke['seq']+1: raise GameError('Часть рисунка не доставлена. Повторите отправку.')
        if sum(len(s['points']) for s in drawing['strokes'])+len(cleaned)>12000: raise GameError('Холст заполнен. Очистите его или используйте бумагу.')
        stroke['points'].extend(cleaned);stroke['seq']=seq
    else: raise GameError('Некорректная команда рисунка.')
    drawing['seen']=(drawing['seen']+[op_id])[-1024:];drawing['revision']+=1


def view(room,index=None):
    validate(room)
    active=room['active'];settings=validate_settings(room['settings'],room['players']) if room['settings'] is not None else None;phase=room['phase']
    sides=[]
    if settings:
        for i,team in enumerate(settings['teams']): sides.append(dict(name=team['name'],members=team['members'],size=team['size'],color=COLORS[i],symbol=SYMBOLS[i]))
    result=dict(game='activity',schemaVersion=SCHEMA,ruleset=RULESET,phase=phase,revision=room['revision'],match=room['match'],
                you=index,server_time=time.time(),players=[dict(name=p['name']) for p in room['players']],settings=settings,
                sides=sides,positions=list(room['positions']),cursor=room['cursor'],turn_number=room['turn_number'],
                categories=CATEGORIES,turn_id=active['id'] if active else None,presenter=active['presenter'] if active else None,
                active_side=active['side'] if active else None,category=(active['category'] or cell_category(room['positions'][active['side']])) if active else None,
                difficulty=active['difficulty'] if active else None,open_round=active['open'] if active else False,
                deadline=room['deadline'],memorize_deadline=room['memorize_deadline'],history=room['history'],
                winners=room['winners'],last_transaction=room['last_transaction'],can_undo=room['undo'] is not None,
                drawing=dict(strokes=room['drawing']['strokes'],revision=room['drawing']['revision']),
                draft=active.get('draft') if active else None,expired=active.get('expired',False) if active else False,
                preview=None,secret_task=None,card_options=None,opponent_card=None)
    if active and active.get('card'):
        card=CARD_BY_ID[active['card']]
        if index==active['presenter']:
            if phase=='choose_task': result['card_options']={key:card['tasks'][key] for key in allowed_categories(room['positions'][active['side']])}
            if active.get('category') and phase in ('prepare','performing'):
                result['secret_task']=card['tasks'][active['category']]
    return result
