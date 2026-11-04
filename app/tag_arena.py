"""Arena layouts, collision geometry and round-time objects for Tag."""
import math

HEART_INTERVAL = 12
HEART_LIFETIME = 9
SHIELD_DURATION = 10
PORTAL_COOLDOWN = 1.4
WALLS = [
    dict(x=330,y=230,w=140,h=30), dict(x=730,y=460,w=140,h=30),
    dict(x=570,y=150,w=30,h=130), dict(x=570,y=440,w=30,h=130),
    dict(x=220,y=390,w=120,h=30), dict(x=870,y=290,w=120,h=30),
    dict(x=140,y=520,w=120,h=30), dict(x=940,y=120,w=120,h=30),
]


def layout(round_number):
    count = min(8, max(0, round_number-1)*2)
    portals = [dict(x=170,y=160,r=28),dict(x=1030,y=550,r=28)] if round_number>=3 else []
    holes = [dict(x=600,y=360,r=28)] if round_number>=4 else []
    if round_number>=6: holes.append(dict(x=960,y=180,r=28))
    return dict(walls=[dict(w) for w in WALLS[:count]],portals=portals,black_holes=holes)


def touches_wall(x,y,wall,radius=20):
    closest_x=max(wall['x'],min(x,wall['x']+wall['w']))
    closest_y=max(wall['y'],min(y,wall['y']+wall['h']))
    return math.hypot(x-closest_x,y-closest_y)<radius-1e-7


def free(x,y,arena,radius=20):
    return (40<=x<=1160 and 40<=y<=650
            and not any(touches_wall(x,y,w,radius) for w in arena['walls']))


def move(position,dx,dy,arena):
    # Short, axis-separated steps stop thin-wall tunnelling and allow sliding.
    steps=max(1,math.ceil(max(abs(dx),abs(dy))/6))
    for _ in range(steps):
        for axis,amount in [('x',dx/steps),('y',dy/steps)]:
            target=dict(position);target[axis]+=amount
            target['x']=max(40,min(1160,target['x']));target['y']=max(40,min(650,target['y']))
            if free(target['x'],target['y'],arena):position.update(target)
            else:
                low,high=0.,1.
                for _ in range(12):
                    fraction=(low+high)/2
                    trial=dict(position);trial[axis]+=(target[axis]-position[axis])*fraction
                    if free(trial['x'],trial['y'],arena):low=fraction
                    else:high=fraction
                position[axis]+=(target[axis]-position[axis])*low


def near(position,obj,radius=None):
    return math.hypot(position['x']-obj['x'],position['y']-obj['y']) <= (obj['r'] if radius is None else radius)


def safe_spot(x,y,arena,others=()):
    objects=arena['portals']+arena['black_holes']
    return (free(x,y,arena,30) and all(math.hypot(x-o['x'],y-o['y'])>o['r']+50 for o in objects)
            and all(math.hypot(x-o['x'],y-o['y'])>75 for o in others))


def spawn_positions(count,arena):
    positions=[]
    for i in range(count):
        x,y=600+260*math.cos(i*2*math.pi/count),360+210*math.sin(i*2*math.pi/count)
        if not safe_spot(x,y,arena,positions):
            x,y=next((x,y) for y in range(100,621,80) for x in range(100,1121,80) if safe_spot(x,y,arena,positions))
        positions.append(dict(x=x,y=y))
    return positions


def ensure(room):
    """Additive defaults keep already running, persisted arenas playable."""
    count=len(room['players'])
    room.setdefault('round',room.get('match',0))
    room.setdefault('arena',layout(1))
    room.setdefault('hearts',[])
    room.setdefault('next_heart',HEART_INTERVAL)
    room.setdefault('heart_serial',0)
    room.setdefault('shields',[0]*count)
    room.setdefault('portal_ready',[0]*count)
    room.setdefault('black_inside',[False]*count)
    room.setdefault('warps',[0]*count)


def objects(room,rng):
    elapsed=room['elapsed'];arena=room['arena']
    room['hearts']=[h for h in room['hearts'] if h['expires']>elapsed]
    if elapsed>=room['next_heart']:
        room['next_heart']=elapsed+HEART_INTERVAL
        spots=[(x,y) for y in range(100,621,80) for x in range(100,1121,80)
               if safe_spot(x,y,arena,room['positions'])]
        if spots and not room['hearts']:
            x,y=rng.choice(spots);room['heart_serial']+=1
            room['hearts'].append(dict(id=room['heart_serial'],x=x,y=y,r=16,expires=elapsed+HEART_LIFETIME))
    entrants=[]
    for i,pos in enumerate(room['positions']):
        if elapsed>=room['portal_ready'][i] and len(arena['portals'])==2:
            for j,portal in enumerate(arena['portals']):
                if near(pos,portal):
                    destination=arena['portals'][1-j]
                    # Fixed clear landing area avoids immediate bounce and wall embedding.
                    pos.update(x=destination['x'],y=destination['y']-65)
                    room['portal_ready'][i]=elapsed+PORTAL_COOLDOWN
                    room['warps'][i]+=1
                    break
        inside=any(near(pos,hole) for hole in arena['black_holes'])
        if inside and not room['black_inside'][i] and i!=room['tagger']:entrants.append(i)
        room['black_inside'][i]=inside
    for heart in list(room['hearts']):
        candidates=[(math.hypot(p['x']-heart['x'],p['y']-heart['y']),i)
                    for i,p in enumerate(room['positions']) if near(p,heart,36)]
        if candidates:
            _,i=min(candidates)
            room['shields'][i]=elapsed+SHIELD_DURATION
            room['hearts'].remove(heart)
    return entrants[0] if entrants else None
