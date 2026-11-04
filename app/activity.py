"""Show & Explain: persistent decks, private presenter tasks and atomic board moves."""
import copy
import math
import secrets
import time

from .durak import GameError
from .activity_data import CARDS, CARD_BY_ID, CATEGORIES, CATEGORY_BY_KEY, DECK_IDS
from .activity_types import GameState, MoveTransaction, TurnResult

MIN_PLAYERS, MAX_PLAYERS = 3, 16
START, FINISH = 0, 49
RULESET = 'show-explain-family-v1'
SCHEMA = 1
RNG = secrets.SystemRandom()
PHASES = {'lobby','handoff','choose_difficulty','choose_task','prepare','memorize','opponent_preview','ready','performing','review','board','finished'}
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
    if not isinstance(settings, dict): raise GameError('Сначала сохраните состав и настройки игры.')
    mode = settings.get('mode')
    precision = settings.get('precision')
    if mode not in ('teams','individual') or not isinstance(precision,str) or not 1<=len(precision.strip())<=200:
        raise GameError('Выберите режим и договоритесь о точности ответов (до 200 символов).')
    count = len(players)
    if mode == 'individual':
        if count != 3: raise GameError('В режиме «Втроём» нужны ровно три игрока.')
        teams = [dict(name=p['name'], members=[i], first_presenter=i) for i,p in enumerate(players)]
    else:
        if not 4<=count<=16: raise GameError('Для командного режима нужны от 4 до 16 игроков.')
        teams = settings.get('teams')
        if not isinstance(teams,list) or not 2<=len(teams)<=4: raise GameError('Нужно от 2 до 4 команд.')
        teams = copy.deepcopy(teams)
        used=[];names=[]
        for team in teams:
            if not isinstance(team,dict): raise GameError('Проверьте состав команд.')
            name=team.get('name');members=team.get('members');first=team.get('first_presenter')
            if not isinstance(name,str) or not 1<=len(name.strip())<=24: raise GameError('Названия команд: от 1 до 24 символов.')
            if (not isinstance(members,list) or len(members)<2 or any(not integer(i,0,count-1) for i in members)
                    or not integer(first,0,count-1) or first not in members):
                raise GameError('В каждой команде нужны минимум двое и выбранный первый ведущий.')
            team['name']=name.strip();used.extend(members);names.append(name.casefold().strip())
        if sorted(used)!=list(range(count)): raise GameError('Каждый игрок должен быть ровно в одной команде.')
        if len(set(names))!=len(names): raise GameError('Названия команд должны различаться.')
    starting=settings.get('starting_side',0)
    if not integer(starting,0,len(teams)-1): raise GameError('Выберите первую команду или игрока.')
    return dict(mode=mode,teams=teams,starting_side=starting,precision=precision.strip())


def validate(room):
    try:
        if room['schemaVersion']!=SCHEMA or room['ruleset']!=RULESET or room['phase'] not in PHASES: raise ValueError()
        if not isinstance(room['players'],list) or len(room['players'])>16 or any(not isinstance(p,dict) or not isinstance(p.get('name'),str) for p in room['players']): raise ValueError()
        if room['phase']=='lobby':
            if room['settings'] is not None: validate_settings(room['settings'],room['players'])
            return
        settings=validate_settings(room['settings'],room['players'])
        count=len(settings['teams'])
        if len(room['positions'])!=count or any(not integer(p,START,FINISH) for p in room['positions']): raise ValueError()
        if not integer(room['cursor'],0,count-1) or len(room['offsets'])!=count: raise ValueError()
        if any(not integer(offset,0,len(settings['teams'][i]['members'])-1) for i,offset in enumerate(room['offsets'])): raise ValueError()
        active=room['active']
        if not isinstance(active,dict) or not integer(active['presenter'],0,len(room['players'])-1) or not integer(active['side'],0,count-1): raise ValueError()
        if active['presenter'] not in settings['teams'][active['side']]['members']: raise ValueError()
        if active['side']!=room['cursor'] and room['phase'] not in ('board','finished'): raise ValueError()
        if active.get('category') is not None and active['category'] not in CATEGORY_BY_KEY: raise ValueError()
        if active.get('card') is not None and (active['card'] not in CARD_BY_ID or CARD_BY_ID[active['card']]['difficulty']!=active.get('difficulty')): raise ValueError()
        if room['phase'] in ('prepare','memorize','opponent_preview','ready','performing','review','board','finished') and (not active.get('card') or active.get('category') not in CATEGORY_BY_KEY): raise ValueError()
        held=active.get('card') if room['phase'] not in ('board','finished') else None
        for d,ids in DECK_IDS.items():
            deck=room['decks'][d]
            all_ids=deck['draw']+deck['discard']+([held] if held and CARD_BY_ID[held]['difficulty']==int(d) else [])
            if sorted(all_ids)!=sorted(ids): raise ValueError()
        clock_key = 'deadline' if room['phase']=='performing' else 'memorize_deadline' if room['phase']=='memorize' else None
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
    settings=validate_settings(room['settings'],room['players'])
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


def select_task(card_id, position, category=None):
    card=CARD_BY_ID[card_id]
    chosen=category if position==START else cell_category(position)
    if chosen not in CATEGORY_BY_KEY: raise GameError('Выберите задание и способ для стартовой клетки.')
    return chosen, copy.deepcopy(card['tasks'][chosen])


def draw_card(room,difficulty,rng=None):
    if not integer(difficulty,3,5): raise GameError('Выберите сложность: 3, 4 или 5.')
    deck=room['decks'][str(difficulty)]
    if not deck['draw']:
        deck['draw']=list(deck['discard']);deck['discard']=[];(rng or RNG).shuffle(deck['draw'])
    if not deck['draw']: raise GameError('В колоде нет карточек.')
    card=deck['draw'].pop();room['active'].update(card=card,difficulty=difficulty)
    position=room['positions'][room['active']['side']]
    if position==START: room['phase']='choose_task'
    else: choose_task(room,cell_category(position))


def choose_task(room,category):
    chosen,task=select_task(room['active']['card'],room['positions'][room['active']['side']],category)
    room['active'].update(category=chosen,open=room['settings']['mode']=='individual' or task['isOpenRound'])
    room['phase']='prepare'


def expire(room,now=None):
    now=time.time() if now is None else now
    changed=False
    if room['phase']=='memorize' and now>=room['memorize_deadline']:
        room.update(phase='ready',memorize_deadline=None);changed=True
    elif room['phase']=='performing' and now>=room['deadline']:
        room['phase']='review';room['active']['expired']=True;changed=True
    if changed: room['revision']+=1
    return changed


def resolve_occupancy(before, advances, collisions=True):
    after=list(before)
    for index,amount in advances.items(): after[index]=min(FINISH,before[index]+amount)
    bumped=[]
    if collisions:
        destinations={after[i] for i in advances if after[i] not in (START,FINISH)}
        for i,position in enumerate(before):
            if i not in advances and position in destinations:
                after[i]=max(START,position-1);bumped.append(i)
    return after,bumped


def build_move_transaction(before,advances,turn_id,collisions=True) -> MoveTransaction:
    after,bumped=resolve_occupancy(before,advances,collisions)
    return dict(turn_id=turn_id,before=list(before),after=after,advances=dict(advances),
                raw_destinations={i:before[i]+amount for i,amount in advances.items()},
                bumped=bumped,winners=[i for i,p in enumerate(after) if p>=FINISH])


def resolve_turn(room,result:TurnResult) -> MoveTransaction:
    active=room['active'];outcome=result.get('outcome');guesser=result.get('guesser')
    if outcome not in ('success','failure','violation'): raise GameError('Выберите исход попытки.')
    if outcome!='success' and guesser is not None: raise GameError('Для неудачи или нарушения не выбирайте угадавшего.')
    advances={}
    if outcome=='success':
        if not integer(guesser,0,len(room['players'])-1) or guesser==active['presenter']:
            raise GameError('Выберите угадавшего игрока; ведущий не может угадать сам.')
        side=next(i for i,team in enumerate(room['settings']['teams']) if guesser in team['members'])
        if room['settings']['mode']=='individual':
            advances={active['side']:active['difficulty'],side:active['difficulty']}
        elif active['open']:
            advances={active['side']:6} if side==active['side'] else {active['side']:2,side:4}
        else:
            if side!=active['side']: raise GameError('В обычном раунде угадывает только команда ведущего.')
            advances={side:active['difficulty']}
    return build_move_transaction(room['positions'],advances,active['id'],room['settings']['mode']=='teams')


def apply_result(room):
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
    if not room['active'] or index!=room['active']['presenter']: raise GameError('Сейчас управляет ведущий этого хода.')
    action=data.get('action');phase=room['phase']
    if action=='take_turn' and phase=='handoff': room['phase']='choose_difficulty'
    elif action=='difficulty' and phase=='choose_difficulty': draw_card(room,data.get('difficulty'),rng)
    elif action=='task' and phase=='choose_task': choose_task(room,data.get('category'))
    elif action=='memorize' and phase=='prepare': room.update(phase='memorize',memorize_deadline=now+10)
    elif action=='ready' and phase in ('prepare','memorize','opponent_preview'):
        room.update(phase='ready',memorize_deadline=None)
    elif action=='opponents' and phase in ('prepare','ready') and not room['active']['open'] and room['settings']['mode']=='teams':
        room.update(phase='opponent_preview',memorize_deadline=None)
    elif action=='perform' and phase=='ready': room.update(phase='performing',deadline=now+60)
    elif action in ('stop','give_up') and phase in ('prepare','memorize','opponent_preview','ready','performing'):
        room['phase']='review';room['memorize_deadline']=None
        if action=='give_up':room['active'].update(gave_up=True,draft=dict(outcome='failure',guesser=None))
    else: raise GameError('Сейчас это действие недоступно.')


def act(room,index,data,now=None,rng=None):
    validate(room)
    if data.get('action')=='draw': draw_action(room,index,data)
    else: presenter_action(room,index,data,now,rng)


def host_act(room,data,now=None,rng=None):
    validate(room);now=time.time() if now is None else now
    action=data.get('action');phase=room['phase']
    if action=='configure' and phase=='lobby': room['settings']=validate_settings(data.get('settings'),room['players'])
    elif action=='next' and phase=='board': begin_turn(room,rng)
    elif action=='perform' and phase=='ready': room.update(phase='performing',deadline=now+60)
    elif action=='stop' and phase=='performing': room['phase']='review'
    elif action=='result' and phase=='review':
        result=dict(outcome=data.get('outcome'),guesser=data.get('guesser'))
        resolve_turn(room,result);room['active']['draft']=result
    elif action=='apply' and phase=='review': apply_result(room)
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
    active=room['active'];settings=room['settings'];phase=room['phase']
    sides=[]
    if settings:
        for i,team in enumerate(settings['teams']): sides.append(dict(name=team['name'],members=team['members'],color=COLORS[i],symbol=SYMBOLS[i]))
    result=dict(game='activity',schemaVersion=SCHEMA,ruleset=RULESET,phase=phase,revision=room['revision'],match=room['match'],
                you=index,server_time=time.time(),players=[dict(name=p['name']) for p in room['players']],settings=settings,
                sides=sides,positions=list(room['positions']),cursor=room['cursor'],turn_number=room['turn_number'],
                categories=CATEGORIES,turn_id=active['id'] if active else None,presenter=active['presenter'] if active else None,
                active_side=active['side'] if active else None,category=active['category'] if active else None,
                difficulty=active['difficulty'] if active else None,open_round=active['open'] if active else False,
                deadline=room['deadline'],memorize_deadline=room['memorize_deadline'],history=room['history'],
                winners=room['winners'],last_transaction=room['last_transaction'],can_undo=room['undo'] is not None,
                drawing=dict(strokes=room['drawing']['strokes'],revision=room['drawing']['revision']),
                draft=active.get('draft') if active else None,expired=active.get('expired',False) if active else False,
                preview=None,secret_task=None,card_options=None,opponent_card=None)
    if active and active.get('draft') and phase=='review': result['preview']=resolve_turn(room,active['draft'])
    if active and active.get('card'):
        card=CARD_BY_ID[active['card']]
        own_side=settings['teams'][active['side']]['members']
        if index==active['presenter']:
            if phase=='choose_task': result['card_options']=card['tasks']
            if active.get('category') and phase in ('prepare','memorize','opponent_preview','ready','review'):
                result['secret_task']=card['tasks'][active['category']]
        elif index is not None and index not in own_side and phase=='opponent_preview' and not active['open']:
            result['opponent_card']=card['tasks']
    return result
