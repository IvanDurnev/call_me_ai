(() => {
  'use strict';
  const $=id=>document.getElementById(id), host=document.body.dataset.mode==='host', code=document.body.dataset.code;
  const storageKey=`activity:${host?'host':'player'}:${code}`, base=`/api/games/rooms/${encodeURIComponent(code)}`;
  let token;try{token=localStorage.getItem(storageKey);}catch{}
  let state=null,busy=false,polling=false,timer,forceRender=false,lastRender='',first=true;
  let secretOpen=false,hasSeen=false,recovery=false,turnKey='',setupDraft=null,setupRoster='';
  let serverOffset=0,confirmCallback=null,layoutKey='',lastPositions=null,previousPhase='';
  let audio=null,beepTurn=null;
  const tokenNodes=new Map(),svgNS='http://www.w3.org/2000/svg';
  const canvas=$('sketch'),ctx=canvas.getContext('2d');
  let tool='pen',width=7,pointer=null,activeStroke=null,inkBusy=false;
  const inkQueue=[],optimistic=new Map();
  const feedback=(message='')=>{$('feedback').textContent=message;};
  const el=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
  const text=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
  const sideName=index=>state.sides[index]?.name||'';
  const playerName=index=>state.players[index]?.name||'';
  const mine=()=>state&&state.you!==null&&state.you===state.presenter;
  const category=()=>state.categories.find(c=>c.key===state.category);
  const uid=()=>Array.from(crypto.getRandomValues(new Uint32Array(3)),v=>v.toString(16)).join('');
  const seconds=deadline=>Math.max(0,Math.ceil(deadline-Date.now()/1000-serverOffset));

  function unlockAudio(){try{const Audio=window.AudioContext||window.webkitAudioContext;if(Audio){audio ||= new Audio();audio.resume().catch(()=>{});}}catch{}}
  function beep(){if(!audio||audio.state!=='running')return;try{const oscillator=audio.createOscillator(),gain=audio.createGain();oscillator.frequency.value=660;gain.gain.setValueAtTime(.0001,audio.currentTime);gain.gain.exponentialRampToValueAtTime(.12,audio.currentTime+.02);gain.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+.45);oscillator.connect(gain);gain.connect(audio.destination);oscillator.start();oscillator.stop(audio.currentTime+.5);}catch{}}
  async function api(path,data){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);try{const response=await fetch(path,{method:data?'POST':'GET',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},...(data?{body:JSON.stringify(data)}:{})});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось выполнить запрос.');return result;}finally{clearTimeout(timeout);}}
  function button(label,action,extra={},primary=false){const e=el('button',label,primary?'durak-primary':'durak-secondary');e.type='button';e.disabled=busy;e.addEventListener('click',()=>send(action,extra));return e;}
  function customButton(label,callback){const e=el('button',label,'durak-secondary');e.type='button';e.disabled=busy;e.addEventListener('click',callback);return e;}
  function confirm(title,message,callback){text('confirm-title',title);text('confirm-message',message);confirmCallback=callback;$('confirm-dialog').showModal();}
  async function send(action,extra={}){if(busy||!state)return;unlockAudio();busy=true;feedback();render(true);try{await api(`${base}/action`,{action,revision:state.revision,match:state.match,turn_id:state.turn_id,...extra});}catch(error){feedback(error.name==='AbortError'?'Нет ответа от сервера. Проверяем состояние хода…':error.message);}finally{busy=false;await poll(true);}}

  function select(name,options,value){const e=el('select');e.name=name;for(const [v,label] of options){const option=el('option',label);option.value=v;e.append(option);}e.value=String(value);return e;}
  function label(caption,input){const e=el('label',caption);e.append(input);return e;}
  function readSetup(){if(!setupDraft)return;const f=$('setup-form');setupDraft.starting_side=Number(f.elements.namedItem('starting')?.value||0);}
  function renderSetup(){
    const roster=state.players.map(p=>p.name).join('\n');
    if(setupRoster===roster&&$('setup-form').children.length)return;
    setupRoster=roster;setupDraft={starting_side:state.settings?.starting_side||0};
    const f=$('setup-form');f.replaceChildren();
    f.append(el('p','Команды подключаются по одному телефону. После хода передавайте телефон следующему ведущему.','activity-setup-note'));
    const teams=el('ul',undefined,'activity-setup-team-list');
    state.players.forEach((p,i)=>{const item=el('li',undefined,'activity-team-tile');item.append(el('span',String(i+1).padStart(2,'0'),'activity-team-icon'),el('span',p.name,'activity-team-name'));teams.append(item);});
    f.append(teams);
    f.append(label('Первая команда',select('starting',state.players.map((p,i)=>[i,p.name]),setupDraft.starting_side)));
    const start=el('button','Начать игру','durak-primary');start.type='submit';start.disabled=busy||state.players.length<2;f.append(start);
    if(state.players.length<2)f.append(el('p','Подключите минимум два телефона: по одному на команду.','activity-setup-note'));
  }
  $('setup-form').addEventListener('input',readSetup);
  $('setup-form').addEventListener('submit',event=>{event.preventDefault();readSetup();send('launch',{settings:{mode:'teams',starting_side:setupDraft.starting_side}});});

  function svg(tag,attrs,content){const e=document.createElementNS(svgNS,tag);for(const [key,value] of Object.entries(attrs))e.setAttribute(key,value);if(content!==undefined)e.textContent=content;return e;}
  function boardLayout(){const columns=matchMedia('(max-width:900px)').matches?5:10;return {columns,width:columns*100,height:Math.ceil(50/columns)*104};}
  function cellPoint(index,layout){const row=Math.floor(index/layout.columns),column=row%2?layout.columns-1-index%layout.columns:index%layout.columns;return {x:column*100+50,y:row*104+52};}
  function renderBoard(){
    const layout=boardLayout(),key=`${layout.columns}`;
    if(layoutKey!==key){layoutKey=key;const field=$('board-svg');field.replaceChildren();field.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);
      const points=Array.from({length:50},(_,i)=>cellPoint(i,layout));field.append(svg('polyline',{points:points.map(p=>`${p.x},${p.y}`).join(' '),fill:'none',stroke:'#c6bdac','stroke-width':8,'stroke-linejoin':'round'}));
      for(let i=0;i<50;i++){
        const p=points[i],c=i===0||i===49?null:state.categories[(i-1)%6];
        field.append(svg('rect',{x:p.x-44,y:p.y-46,width:88,height:92,rx:15,fill:c?c.hex:'#e2d6bb',stroke:'#ffffff','stroke-width':2}));
        field.append(svg('text',{x:p.x-32,y:p.y-29,'font-size':11,'font-family':'sans-serif',fill:'#ffffff','font-weight':700},i===0?'СТАРТ':i===49?'ФИНИШ':String(i)));
        field.append(svg('text',{x:p.x,y:p.y-9,'text-anchor':'middle','font-size':24,'font-family':'sans-serif',fill:'#fff'},c?c.icon:i===0?'↗':'★'));
        field.append(svg('text',{x:p.x,y:p.y+9,'text-anchor':'middle','font-size':10,'font-family':'sans-serif',fill:'#fff','font-weight':700},c?c.label:''));
      }
    }
    const tokens=$('tokens');const changed=lastPositions&&JSON.stringify(lastPositions)!==JSON.stringify(state.positions);const before=lastPositions;lastPositions=[...state.positions];
    for(const [i,e] of tokenNodes)if(i>=state.sides.length){e.remove();tokenNodes.delete(i);}
    state.sides.forEach((side,index)=>{
      let e=tokenNodes.get(index);if(!e){e=el('span',`${index+1}${side.symbol}`,'activity-token');tokens.append(e);tokenNodes.set(index,e);}
      e.style.background=side.color;e.title=`${side.name}: ${state.positions[index]===49?'финиш':state.positions[index]}`;e.setAttribute('aria-label',e.title);
      const position=(list)=>{const cell=list?.[index]??0,p=cellPoint(cell,layout),group=state.positions.map((v,i)=>v===cell?i:-1).filter(i=>i>=0),slot=Math.max(0,group.indexOf(index)),offsets=[[-17,18],[17,18],[-17,41],[17,41]],offset=offsets[slot]||[0,28];e.style.left=`${(p.x+offset[0])/layout.width*100}%`;e.style.top=`${(p.y+offset[1])/layout.height*100}%`;};
      if(changed&&!matchMedia('(prefers-reduced-motion:reduce)').matches){position(before);requestAnimationFrame(()=>requestAnimationFrame(()=>position(state.positions)));}else position(state.positions);
    });
    $('board-legend').replaceChildren();state.sides.forEach((side,i)=>{const item=el('span');const dot=el('i');dot.style.background=side.color;item.append(dot,document.createTextNode(`${i+1}${side.symbol} ${side.name} · ${state.positions[i]===49?'Финиш':state.positions[i]}`));$('board-legend').append(item);});
  }

  function canGuess(){return !!state&&mine()&&!recovery&&!busy&&state.phase==='performing'&&seconds(state.deadline)>0;}
  function renderSecret(){
    const available=!recovery&&(state.secret_task||state.card_options),performing=state.phase==='performing';
    const visible=!!state.card_options||secretOpen||performing;
    $('secret-panel').hidden=!available;$('secret-content').hidden=!visible;$('secret-content').replaceChildren();$('secret-actions').replaceChildren();
    const instruction=category()?.method;text('secret-title',instruction==='draw'?'Нарисуйте':instruction==='mime'?'Покажите':'Объясните');
    if(!available||!visible)return;
    if(state.card_options){for(const c of state.categories){const task=state.card_options[c.key];if(!task)continue;const b=button('','task',{category:c.key});b.className='task-choice';b.append(el('small',`${c.icon} ${c.label}`),el('span',task.text));$('secret-content').append(b);}}
    else if(state.secret_task){
      $('secret-content').append(el('strong',state.secret_task.text));
      if(performing){const guessed=button('Угадано!','guessed',{},true);guessed.dataset.guessed='true';guessed.disabled=!canGuess();$('secret-actions').append(guessed);}
    }
  }

  function previewText(transaction){
    if(!transaction)return '';
    const entries=Object.entries(transaction.advances);if(!entries.length)return 'Без продвижения. Очередь команд перейдёт дальше.';
    const result=entries.map(([i,amount])=>`${sideName(Number(i))}: +${amount}, ${transaction.before[i]} → ${transaction.after[i]===49?'финиш':transaction.after[i]}`);
    if(transaction.bumped.length)result.push(`На клетку назад: ${transaction.bumped.map(sideName).join(', ')}`);
    if(transaction.winners.length)result.push(`Финиш: ${transaction.winners.map(sideName).join(', ')}`);
    return result.join('\n');
  }
  function lastResultText(){const r=state.last_result;if(!r)return '';return r.outcome==='success'?`${sideName(r.side)}: угадано, +${Object.values(r.transaction.advances)[0]||0}. `:`${sideName(r.side)}: ${r.expired?'время вышло':'попытка завершена'}, без продвижения. `;}
  function renderControls(){
    const c=$('controls');c.replaceChildren();if(recovery)return;
    const phase=state.phase;
    if(host){
      if(phase==='finished')c.append(button('Сыграть ещё раз','start',{},true));
      if(state.can_undo)c.append(customButton('Отменить последний ход',()=>confirm('Отменить последний ход?','Вернутся позиции и очередь команд. Текущая незавершённая попытка тоже сбросится.',()=>send('undo'))));
      if(phase!=='lobby')c.append(customButton('Вернуться в лобби',()=>confirm('Вернуться в лобби?','Партия завершится. Команды останутся в комнате.',()=>send('reset'))));
    }
    if(mine()){
      if(phase==='handoff')c.append(button('Показать карточку','take_turn',{},true));
      if(phase==='choose_difficulty')for(const [difficulty,name] of [[3,'Легко'],[4,'Средне'],[5,'Сложно']])c.append(button(`${name} · +${difficulty}`,'difficulty',{difficulty},true));
      if(phase==='prepare'){c.append(el('p','Нажмите «Начать», когда команда будет готова.','activity-start-hint'));const start=button('Начать','perform',{},true);start.classList.add('activity-start-turn');start.disabled=busy;c.append(start);}
      if(['prepare','performing'].includes(phase))c.append(button('Сдаюсь','give_up'));
    }
    $('preview').hidden=true;
  }

  function drawingView(){
    if(!state||state.phase!=='performing'||category()?.method!=='draw')return null;
    if(host)return 'table';
    return mine()&&!recovery?'phone':null;
  }

  function render(force=false){
    if(!state)return;
    const key=`${state.match}:${state.turn_id}`;
    if(turnKey!==key){if(turnKey)recovery=false;turnKey=key;secretOpen=false;hasSeen=false;optimistic.clear();inkQueue.length=0;activeStroke=null;pointer=null;}
    $('join-panel').hidden=true;$('room').hidden=false;
    text('room-code',`Комната ${code} · ${state.players.length}/4 команд`);
    const phase=state.phase;document.body.classList.toggle('is-lobby',phase==='lobby');if(['lobby','handoff','board','finished'].includes(phase))recovery=false;
    if(phase==='prepare'){secretOpen=true;hasSeen=true;}
    const method=category(),view=drawingView(),drawing=!!view;
    document.body.classList.toggle('is-table-drawing',view==='table');
    if(previousPhase!==phase){if(phase==='performing'){$('board-details').open=host;}if(phase==='lobby')$('board-details').open=false;else if(host&&['handoff','board','finished'].includes(phase))$('board-details').open=true;if(previousPhase==='performing'&&state.last_result?.expired&&beepTurn!==state.last_result.turn_id){beepTurn=state.last_result.turn_id;beep();}previousPhase=phase;}
    $('setup-panel').hidden=!host||phase!=='lobby';$('board-details').hidden=phase==='lobby';$('board-panel').hidden=!host&&phase==='lobby';
    const phoneDrawing=view==='phone';
    const target=phoneDrawing?$('secret-drawing-slot'):$('table-drawing-slot');
    if($('sketch-panel').parentElement!==target)target.append($('sketch-panel'));
    $('secret-drawing-slot').hidden=!phoneDrawing;
    $('sketch-panel').hidden=!drawing;$('sketch-tools').hidden=!mine()||phase!=='performing';document.body.classList.toggle('is-drawing',drawing&&mine()&&phase==='performing');
    const renderKey=JSON.stringify([state.revision-state.drawing.revision,phase,state.turn_id,secretOpen,hasSeen,recovery,busy,state.players.map(p=>p.online)]);
    if(force||lastRender!==renderKey){
      lastRender=renderKey;
      const titles={lobby:['Один телефон на команду','Собираемся играть',host?'Подключитесь справа и передавайте телефон внутри команды.':'Ждём старта на общем экране. Передавайте этот телефон ведущему вашей команды.'],handoff:['Передайте телефон',`Ход команды «${playerName(state.presenter)}»`,`${lastResultText()}Нажмите «Показать карточку», чтобы выбрать задание.`],choose_difficulty:['Сложность','Выберите колоду','Легко: +3, средне: +4, сложно: +5.'],choose_task:['Карточка ведущего',method?`Выберите, что будете ${method.method==='draw'?'рисовать':method.method==='mime'?'показывать':'объяснять'}.`:'Выберите, что будете делать: рисовать, показывать или объяснять.'],prepare:['Подготовка','Задание выбрано','Нажмите «Начать», когда команда готова.'],performing:['Угадывает своя команда',method?.label||'Выполнение','На телефоне ведущего видно задание. Нажмите «Угадано!» до конца минуты.'],board:[state.expired?'Время вышло':'Ход завершён',state.last_transaction&&Object.keys(state.last_transaction.advances).length?'Угадано!':'Без продвижения',state.last_transaction?previewText(state.last_transaction):''],finished:['Финиш!',`Победила команда ${state.winners.map(sideName).join(', ')}`,'Сыграем ещё?']};
      const t=titles[phase];text('phase-label',phase==='prepare'?'':t[0]);text('turn-title',t[1]);text('turn-message',phase==='prepare'?'':t[2]);
      $('method-badge').hidden=!method;text('method-badge',method?`${method.icon} ${method.label}${Number.isInteger(state.difficulty)?` · +${state.difficulty}`:''}`:'');
      $('recovery').hidden=!recovery;
      $('turn-panel').hidden=host&&phase==='lobby';
      if(host&&phase==='lobby')renderSetup();
      renderSecret();renderControls();renderBoard();renderRoster();
      $('invite').hidden=!host||phase!=='lobby';if(host&&phase==='lobby'){if(!$('qr').getAttribute('src'))$('qr').src=`${base}/qr.svg`;text('invite-code',code);$('invite-link').textContent=state.join_url.replace(/^https?:\/\//,'');$('invite-link').href=state.join_url;}
    }
    renderSketch();updateClock();
  }
  function renderRoster(){
    $('players').replaceChildren();
    if(state.phase!=='lobby'&&state.sides.length)state.sides.forEach((side,i)=>{const group=el('div',undefined,'activity-side');group.style.setProperty('--side-color',side.color);group.append(el('strong',`${i+1}${side.symbol} ${side.name}${state.positions[i]!==undefined?` · ${state.positions[i]===49?'Финиш':state.positions[i]}`:''}`),el('p',`${side.size} игроков · 1 телефон`));$('players').append(group);});
    else state.players.forEach((player,i)=>{const row=el('div',undefined,'activity-connected-team');row.append(el('span',`${i+1}. ${player.name}`),el('small',player.online?'На связи':'Подключается…'));if(host&&state.phase==='lobby'){const remove=button('×','remove',{target:i});remove.setAttribute('aria-label',`Убрать команду ${player.name}`);row.append(remove);}$('players').append(row);});
  }
  function updateClock(){if(!state)return;const deadline=state.phase==='performing'?state.deadline:null;$('timer').hidden=!deadline;if(deadline){const n=seconds(deadline);text('timer',`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`);for(const b of document.querySelectorAll('[data-guessed]'))b.disabled=!canGuess();if(!n&&state.phase==='performing'&&beepTurn!==state.turn_id){beepTurn=state.turn_id;beep();poll(true);}}}
  setInterval(updateClock,200);

  function strokePath(stroke){const points=stroke.points;if(!points.length)return;ctx.strokeStyle=stroke.tool==='eraser'?'#fff':'#26344a';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=stroke.width;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(points[0][0]*canvas.width,points[0][1]*canvas.height);for(const point of points.slice(1))ctx.lineTo(point[0]*canvas.width,point[1]*canvas.height);if(points.length===1){ctx.arc(points[0][0]*canvas.width,points[0][1]*canvas.height,stroke.width/2,0,Math.PI*2);ctx.fill();}else ctx.stroke();}
  function renderSketch(){if(!state)return;ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);for(const stroke of state.drawing.strokes)strokePath(stroke);for(const [id,stroke] of optimistic){const remote=state.drawing.strokes.find(s=>s.id===id);if(stroke.complete&&remote&&remote.seq>=stroke.seq-1&&!inkQueue.some(op=>op.stroke_id===id)){optimistic.delete(id);continue;}strokePath(stroke);}}
  function point(event){const rect=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];}
  function canDraw(){return state&&mine()&&state.phase==='performing'&&category()?.method==='draw'&&!recovery;}
  function enqueue(op){inkQueue.push({action:'draw',revision:state.revision,match:state.match,turn_id:state.turn_id,op_id:uid(),...op});processInk();}
  function flushStroke(){if(!activeStroke||!activeStroke.buffer.length)return;while(activeStroke.buffer.length){const points=activeStroke.buffer.splice(0,128);enqueue({kind:'stroke',stroke_id:activeStroke.id,seq:activeStroke.seq++,tool:activeStroke.tool,width:activeStroke.width,points});}}
  async function processInk(){if(inkBusy)return;inkBusy=true;$('retry-drawing').hidden=true;try{while(inkQueue.length){const op=inkQueue[0];if(!canDraw()||op.turn_id!==state.turn_id){if(inkQueue[0]===op)inkQueue.shift();continue;}let accepted=false;for(let attempt=0;attempt<3;attempt++){try{await api(`${base}/action`,op);accepted=true;break;}catch(error){if(!canDraw()||op.turn_id!==state.turn_id){accepted=true;break;}if(attempt===2){text('ink-status',`Часть рисунка не отправлена: ${error.name==='AbortError'?'нет связи':error.message}`);$('retry-drawing').hidden=false;return;}}}if(accepted&&inkQueue[0]===op)inkQueue.shift();}text('ink-status','Рисунок сохранён');poll(false);}finally{inkBusy=false;}}
  canvas.addEventListener('pointerdown',event=>{if(!canDraw()||pointer!==null)return;unlockAudio();event.preventDefault();pointer=event.pointerId;canvas.setPointerCapture(pointer);const start=point(event);activeStroke={id:uid(),tool,width,points:[start],buffer:[start],seq:0,complete:false};optimistic.set(activeStroke.id,activeStroke);renderSketch();});
  canvas.addEventListener('pointermove',event=>{if(pointer!==event.pointerId||!activeStroke)return;event.preventDefault();const p=point(event);activeStroke.points.push(p);activeStroke.buffer.push(p);renderSketch();});
  function endStroke(event){if(pointer!==event.pointerId||!activeStroke)return;flushStroke();activeStroke.complete=true;activeStroke=null;pointer=null;renderSketch();}
  for(const kind of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(kind,endStroke);
  setInterval(()=>{if(activeStroke)flushStroke();},150);
  $('pen').addEventListener('click',()=>{tool='pen';$('pen').setAttribute('aria-pressed','true');$('eraser').setAttribute('aria-pressed','false');});
  $('eraser').addEventListener('click',()=>{tool='eraser';$('pen').setAttribute('aria-pressed','false');$('eraser').setAttribute('aria-pressed','true');});
  $('pen-width').addEventListener('change',event=>{width=Number(event.target.value);});
  $('undo-stroke').addEventListener('click',()=>{if(canDraw())enqueue({kind:'undo'});});
  $('clear-drawing').addEventListener('click',()=>confirm('Очистить рисунок?','Все штрихи текущей попытки будут убраны.',()=>{if(canDraw()){optimistic.clear();enqueue({kind:'clear'});}}));
  $('retry-drawing').addEventListener('click',processInk);

  async function poll(force=false){clearTimeout(timer);if(force)forceRender=true;if(!token||polling)return;polling=true;try{const next=await api(base);if(next.game!=='activity')throw new Error('Это комната другой игры. Откройте её через игротеку.');state=next;serverOffset=state.server_time-Date.now()/1000;
      if(first){first=false;$('board-details').open=host;$('roster-details').open=host;recovery=mine()&&!['lobby','handoff','board','finished'].includes(state.phase);}
      text('connection','● На связи');$('connection').className='connection-ok';render(forceRender);forceRender=false;
    }catch(error){text('connection','Переподключаемся…');$('connection').className='connection-lost';feedback(error.name==='AbortError'?'Нет ответа от сервера. Восстанавливаем связь…':error.message);if(error.message.includes('повреждено')||error.message.includes('несовместимо')){$('corrupt-panel').hidden=false;$('room').hidden=true;}if(error.message==='Подключитесь к комнате заново.'){token=null;localStorage.removeItem(storageKey);$('room').hidden=true;if(!host)$('join-panel').hidden=false;}}
    finally{polling=false;if(token)timer=setTimeout(poll,state?.phase==='performing'&&category()?.method==='draw'?250:900);}}
  $('join-form').addEventListener('submit',async event=>{event.preventDefault();unlockAudio();feedback();$('join-submit').disabled=true;try{const result=await api(`${base}/join`,{name:$('player-name').value.trim()});localStorage.setItem(storageKey,result.token);token=result.token;await poll(true);}catch(error){feedback(error.name==='AbortError'?'Нет связи с сервером.':error.message);}finally{$('join-submit').disabled=false;}});

  $('continue-recovery').addEventListener('click',()=>{recovery=false;render(true);});
  $('confirm-cancel').addEventListener('click',()=>$('confirm-dialog').close());$('confirm-accept').addEventListener('click',()=>{$('confirm-dialog').close();const callback=confirmCallback;confirmCallback=null;callback?.();});
  $('help-button').addEventListener('click',()=>$('help-dialog').showModal());
  if(document.fullscreenEnabled&&host){$('fullscreen').hidden=false;$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{feedback('Включите полный экран через меню браузера.');}});}
  window.addEventListener('online',()=>{processInk();poll(true);});window.addEventListener('resize',()=>{layoutKey='';if(state)renderBoard();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){secretOpen=false;if(state)renderSecret();flushStroke();}else poll(true);});
  if(token){$('room').hidden=false;poll(true);}else if(!host)$('join-panel').hidden=false;else feedback('Создайте комнату в игротеке на этом устройстве.');
})();
