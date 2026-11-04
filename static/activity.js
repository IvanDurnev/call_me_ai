(() => {
  'use strict';
  const $=id=>document.getElementById(id), host=document.body.dataset.mode==='host', code=document.body.dataset.code;
  const storageKey=`activity:${host?'host':'player'}:${code}`, base=`/api/games/rooms/${encodeURIComponent(code)}`;
  let token;try{token=localStorage.getItem(storageKey);}catch{}
  let state=null,busy=false,polling=false,timer,forceRender=false,lastRender='',first=true;
  let secretOpen=false,hasSeen=false,recovery=false,turnKey='',reviewDraft=null,setupDraft=null,setupRoster='';
  let serverOffset=0,confirmCallback=null,layoutKey='',lastPositions=null,previousPhase='';
  let audio=null,sound=true,beepTurn=null;
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

  function unlockAudio(){if(!sound)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(Audio){audio ||= new Audio();audio.resume().catch(()=>{});}}catch{}}
  function beep(){if(!sound||!audio||audio.state!=='running')return;try{const oscillator=audio.createOscillator(),gain=audio.createGain();oscillator.frequency.value=660;gain.gain.setValueAtTime(.0001,audio.currentTime);gain.gain.exponentialRampToValueAtTime(.12,audio.currentTime+.02);gain.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+.45);oscillator.connect(gain);gain.connect(audio.destination);oscillator.start();oscillator.stop(audio.currentTime+.5);}catch{}}
  async function api(path,data){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);try{const response=await fetch(path,{method:data?'POST':'GET',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},...(data?{body:JSON.stringify(data)}:{})});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось выполнить запрос.');return result;}finally{clearTimeout(timeout);}}
  function button(label,action,extra={},primary=false){const e=el('button',label,primary?'durak-primary':'durak-secondary');e.type='button';e.disabled=busy;e.addEventListener('click',()=>send(action,extra));return e;}
  function customButton(label,callback){const e=el('button',label,'durak-secondary');e.type='button';e.disabled=busy;e.addEventListener('click',callback);return e;}
  function confirm(title,message,callback){text('confirm-title',title);text('confirm-message',message);confirmCallback=callback;$('confirm-dialog').showModal();}
  async function send(action,extra={}){if(busy||!state)return;unlockAudio();busy=true;feedback();render(true);try{await api(`${base}/action`,{action,revision:state.revision,match:state.match,turn_id:state.turn_id,...extra});}catch(error){feedback(error.name==='AbortError'?'Нет ответа от сервера. Проверяем состояние хода…':error.message);}finally{busy=false;await poll(true);}}

  function defaultSetup(){
    const settings=state.settings;
    if(settings)return {mode:settings.mode,count:settings.teams.length,names:settings.teams.map(t=>t.name),assign:state.players.map((_,i)=>settings.teams.findIndex(t=>t.members.includes(i))),first:settings.teams.map(t=>t.first_presenter),starting:settings.starting_side,precision:settings.precision};
    return {mode:state.players.length===3?'individual':'teams',count:2,names:['Синие','Кораллы','Лесные','Звёздные'],assign:state.players.map((_,i)=>i%2),first:[0,1,2,3],starting:0,precision:'Достаточно точного смысла; спорные ответы обсуждаем вместе.'};
  }
  function select(name,options,value){const e=el('select');e.name=name;for(const [v,label] of options){const option=el('option',label);option.value=v;e.append(option);}e.value=String(value);return e;}
  function label(caption,input){const e=el('label',caption);e.append(input);return e;}
  function readSetup(){if(!setupDraft)return;const f=$('setup-form');setupDraft.mode=f.elements.namedItem('game-mode')?.value||setupDraft.mode;const n=f.elements.namedItem('team-count');if(n)setupDraft.count=Number(n.value);const precision=f.elements.namedItem('precision');if(precision)setupDraft.precision=precision.value;const starting=f.elements.namedItem('starting');if(starting)setupDraft.starting=Number(starting.value);for(let i=0;i<4;i++){const name=f.elements.namedItem(`team-name-${i}`),first=f.elements.namedItem(`first-${i}`);if(name)setupDraft.names[i]=name.value;if(first)setupDraft.first[i]=Number(first.value);}state.players.forEach((_,i)=>{const assignment=f.elements.namedItem(`assignment-${i}`);if(assignment)setupDraft.assign[i]=Number(assignment.value);});}
  function renderSetup(force=false){
    const roster=state.players.map(p=>p.name).join('\n');
    if(setupRoster!==roster||!setupDraft){setupDraft=defaultSetup();setupRoster=roster;force=true;}
    if(!force&&$('setup-form').children.length)return;
    const f=$('setup-form');f.replaceChildren();const d=setupDraft;
    d.assign=d.assign.map(i=>Math.min(d.count-1,Math.max(0,i)));d.starting=Math.min(d.starting,(d.mode==='individual'?3:d.count)-1);
    const row=el('div',undefined,'activity-setup-row');row.append(label('Режим',select('game-mode',[['teams','Команды: 4–16 человек'],['individual','Втроём: каждый за себя']],d.mode)));
    if(d.mode==='teams')row.append(label('Количество команд',select('team-count',[[2,'2 команды'],[3,'3 команды'],[4,'4 команды']],d.count)));f.append(row);
    if(d.mode==='teams'){
      const assignments=el('div',undefined,'activity-assignment');state.players.forEach((p,i)=>assignments.append(label(p.name,select(`assignment-${i}`,Array.from({length:d.count},(_,j)=>[j,`${j+1}. ${d.names[j]||`Команда ${j+1}`}`]),d.assign[i]||0))));f.append(assignments);
      for(let i=0;i<d.count;i++){
        const group=el('div',undefined,'activity-team-config'),input=el('input');input.name=`team-name-${i}`;input.maxLength=24;input.required=true;input.value=d.names[i]||`Команда ${i+1}`;
        const members=state.players.map((p,j)=>[j,p.name]).filter(([j])=>d.assign[j]===i);if(!members.some(([j])=>j===d.first[i]))d.first[i]=members[0]?.[0]??0;
        group.append(label(`Название команды ${i+1}`,input),label('Первый ведущий',select(`first-${i}`,members.length?members:[['','Нет игроков']],d.first[i])));f.append(group);
      }
    }
    const names=d.mode==='individual'?state.players.slice(0,3).map(p=>p.name):d.names.slice(0,d.count);
    f.append(label('Кто начинает',select('starting',names.map((n,i)=>[i,n||`Команда ${i+1}`]),d.starting)));
    const precision=el('textarea');precision.name='precision';precision.maxLength=200;precision.required=true;precision.rows=3;precision.value=d.precision;f.append(label('Какую точность ответа принимаем?',precision));
    f.append(el('p',d.mode==='individual'?'Нужны ровно три игрока. У каждого своя фишка; угадывают оба соперника.':'В каждой команде минимум двое. Порядок команд фиксирован; ведущие внутри команды чередуются.','activity-setup-note'));
    const save=el('button','Сохранить состав','durak-primary');save.type='submit';save.disabled=busy;f.append(save);
  }
  $('setup-form').addEventListener('input',readSetup);
  $('setup-form').addEventListener('change',event=>{readSetup();if(['game-mode','team-count'].includes(event.target.name)||event.target.name.startsWith('assignment-'))renderSetup(true);});
  $('setup-form').addEventListener('submit',event=>{event.preventDefault();readSetup();const d=setupDraft;const teams=Array.from({length:d.count},(_,i)=>({name:d.names[i],members:state.players.map((_,j)=>j).filter(j=>d.assign[j]===i),first_presenter:d.first[i]}));send('configure',{settings:{mode:d.mode,teams,starting_side:d.starting,precision:d.precision}});});

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

  function renderSecret(){
    const available=!recovery&&(state.secret_task||state.card_options||state.opponent_card);
    $('secret-panel').hidden=!available;$('secret-content').hidden=!secretOpen;$('reveal-secret').hidden=secretOpen;$('hide-secret').hidden=!secretOpen;
    $('reveal-secret').disabled=busy;$('hide-secret').disabled=busy;$('secret-content').replaceChildren();
    text('secret-title',state.opponent_card?'Карточка для соперников':'Только ведущему');
    if(!available||!secretOpen)return;
    if(state.card_options||state.opponent_card){
      for(const c of state.categories){const task=(state.card_options||state.opponent_card)[c.key];
        if(state.card_options){const b=button('', 'task',{category:c.key});b.className='task-choice';b.append(el('small',`${c.icon} ${c.label} · ${({amber:'янтарный',blue:'синий',green:'зелёный',violet:'фиолетовый',coral:'коралловый',teal:'бирюзовый'})[c.color]}${task.isOpenRound?' · Открытый раунд':''}`),el('span',task.text));$('secret-content').append(b);}
        else {const item=el('p');item.append(el('small',`${c.icon} ${c.label}${c.key===state.category?' · ТЕКУЩЕЕ ЗАДАНИЕ':''}`),el('strong',task.text));$('secret-content').append(item);}
      }
      if(state.opponent_card)$('secret-content').append(el('p','Не показывайте экран команде ведущего. После закрытия показа карточка исчезнет.'));
    }else if(state.secret_task){$('secret-content').append(el('strong',state.secret_task.text));if(state.open_round)$('secret-content').append(el('span','Открытый раунд: угадывают все','activity-open-label'));$('secret-content').append(el('p','Не показывайте задание своей команде. Во время выполнения слово будет скрыто.'));}
  }

  function previewText(transaction){
    if(!transaction)return '';
    const entries=Object.entries(transaction.advances);if(!entries.length)return 'Без продвижения. Очередь ведущих перейдёт дальше.';
    const result=entries.map(([i,amount])=>`${sideName(Number(i))}: +${amount}, ${transaction.before[i]} → ${transaction.after[i]===49?'финиш':transaction.after[i]}`);
    if(transaction.bumped.length)result.push(`На клетку назад: ${transaction.bumped.map(sideName).join(', ')}`);
    if(transaction.winners.length)result.push(`Финиш: ${transaction.winners.map(sideName).join(', ')}`);
    return result.join('\n');
  }
  function renderReview(container){
    reviewDraft ||= state.draft?{...state.draft}:{outcome:'failure',guesser:null};
    const outcome=select('outcome',[['failure','Не угадали'],['success','Угадали вовремя'],['violation','Нарушение правил ведущим']],reviewDraft.outcome);
    outcome.addEventListener('change',()=>{reviewDraft.outcome=outcome.value;reviewDraft.guesser=null;renderControls();});container.append(label('Как закончилась попытка?',outcome));
    if(reviewDraft.outcome==='success'){
      const candidates=state.players.map((p,i)=>[i,p.name]).filter(([i])=>i!==state.presenter&&(state.settings.mode==='individual'||state.open_round||state.sides[state.active_side].members.includes(i)));
      const guess=select('guesser',[['','Выберите угадавшего'],...candidates.map(([i,name])=>[i,`${name} · ${sideName(state.sides.findIndex(s=>s.members.includes(i)))}`])],reviewDraft.guesser??'');
      guess.addEventListener('change',()=>{reviewDraft.guesser=guess.value===''?null:Number(guess.value);renderControls();});container.append(label('Кто дал правильный ответ?',guess));
    }
    container.append(el('p','Подтвердите, что ответ прозвучал вовремя и соответствует вашей договорённости. Одновременные ответы сначала обсудите.'));
    const calculate=button('Проверить расчёт','result',reviewDraft);calculate.disabled=busy||(reviewDraft.outcome==='success'&&reviewDraft.guesser===null);container.append(calculate);
    const same=state.draft&&state.draft.outcome===reviewDraft.outcome&&state.draft.guesser===reviewDraft.guesser;
    const apply=button('Подтвердить ход','apply',{},true);apply.disabled=busy||!same;container.append(apply);
    if(state.draft&&!same)container.append(el('p','Результат изменён — сначала обновите расчёт.'));
  }
  function renderControls(){
    const c=$('controls');c.replaceChildren();if(recovery)return;
    const phase=state.phase;
    if(host){
      if(phase==='lobby'){const start=button('Начать игру →','start',{},true);start.disabled=busy||!state.settings;c.append(start);}
      if(phase==='ready')c.append(button('Начать минуту','perform',{},true));
      if(phase==='performing')c.append(button('Остановить и разобрать','stop',{},true));
      if(phase==='review')renderReview(c);
      if(phase==='board')c.append(button('Следующий ход →','next',{},true));
      if(phase==='finished')c.append(button('Сыграть ещё раз →','start',{},true));
      if(state.can_undo)c.append(customButton('Отменить последний ход',()=>confirm('Отменить весь последний ход?','Вернутся позиции, очередь, ведущий и колода. Текущая незавершённая попытка тоже сбросится.',()=>send('undo'))));
      if(phase!=='lobby')c.append(customButton('Вернуться в лобби',()=>confirm('Вернуться в лобби?','Партия завершится. Все игроки останутся в комнате.',()=>send('reset'))));
    }
    if(mine()){
      if(phase==='handoff')c.append(button(`Я ${playerName(state.presenter)}, взять ход`,'take_turn',{},true));
      if(phase==='choose_difficulty')for(const [difficulty,name] of [[3,'Легко'],[4,'Средне'],[5,'Сложно']])c.append(button(`${name} · продвижение +${difficulty}`,'difficulty',{difficulty},true));
      if(phase==='prepare'){const memo=button('Запоминать 10 секунд','memorize');memo.disabled=busy||!hasSeen;c.append(memo);}
      if(['prepare','memorize','opponent_preview'].includes(phase)){const ready=button(phase==='opponent_preview'?'Закрыть показ, я готов':'Я запомнил(а), готов','ready',{},true);ready.disabled=busy||!hasSeen;c.append(ready);}
      if(['prepare','ready'].includes(phase)&&!state.open_round&&state.settings.mode==='teams')c.append(button('Показать карточку соперникам','opponents'));
      if(phase==='ready')c.append(button('Начать минуту','perform',{},true));
      if(phase==='performing')c.append(button('Ответ прозвучал — остановить','stop',{},true));
      if(['prepare','memorize','opponent_preview','ready','performing'].includes(phase))c.append(button('Сдаюсь','give_up'));
    }
    $('preview').hidden=!state.preview;
    $('preview').textContent=state.preview?previewText(state.preview):'';$('preview').style.whiteSpace='pre-line';
  }

  function render(force=false){
    if(!state)return;
    const key=`${state.match}:${state.turn_id}`;
    if(turnKey!==key){if(turnKey)recovery=false;turnKey=key;secretOpen=false;hasSeen=false;reviewDraft=null;optimistic.clear();inkQueue.length=0;activeStroke=null;pointer=null;}
    $('join-panel').hidden=true;$('room').hidden=false;
    text('room-code',`Комната ${code} · ${state.players.length}/16 игроков`);
    const phase=state.phase;if(['lobby','handoff','board','finished'].includes(phase))recovery=false;
    const method=category(),drawing=method?.method==='draw'&&['performing','review'].includes(phase);
    if(previousPhase!==phase){if(phase==='performing'){$('board-details').open=!drawing&&host;secretOpen=false;}if(phase==='lobby')$('board-details').open=false;else if(host&&['handoff','board','finished'].includes(phase))$('board-details').open=true;if(phase==='review'&&previousPhase==='performing'&&state.expired&&beepTurn!==state.turn_id){beepTurn=state.turn_id;beep();}previousPhase=phase;}
    $('setup-panel').hidden=!host||phase!=='lobby';$('board-details').hidden=phase==='lobby';$('board-panel').hidden=!host&&phase==='lobby';
    $('sketch-panel').hidden=!drawing;$('sketch-tools').hidden=!mine()||phase!=='performing';document.body.classList.toggle('is-drawing',drawing&&mine()&&phase==='performing');
    const renderKey=JSON.stringify([state.revision-state.drawing.revision,phase,state.turn_id,secretOpen,hasSeen,recovery,busy]);
    if(force||lastRender!==renderKey){
      lastRender=renderKey;
      const titles={lobby:['Рисуем, объясняем, показываем','Собираемся играть','Подключите игроков. Состав и точность ответов настраиваем на общем экране.'],handoff:['Передача хода',`Сейчас ${playerName(state.presenter)}`,`${sideName(state.active_side)}. Ведущий берёт ход на своём телефоне; задание пока скрыто.`],choose_difficulty:['Выбор сложности','Какой будет ход?','Ведущий выбирает колоду на телефоне. После получения карточки заменить её нельзя.'],choose_task:['Стартовая клетка','Выберите одно задание','На старте ведущий может выбрать любой из шести способов на своей карточке.'],prepare:['Подготовка','Запомните задание','Задание доступно только на телефоне ведущего.'],memorize:['Запоминание','Примерно десять секунд','Можно закончить запоминание раньше кнопкой готовности.'],opponent_preview:['Контролируемый показ','Карточка у соперников','Соперники могут открыть карточку на своих телефонах. Команда ведущего её не видит.'],ready:['Все готовы','Одна попытка. Одна минута.','Закройте показ соперникам и начните выполнение.'],performing:[state.open_round?'Открытый раунд':'Угадывает команда ведущего',method?.label||'Выполнение',state.open_round?'Угадывают все, кроме ведущего. Карточка остаётся секретной.':'Слово скрыто. Ответ произносите вслух.'],review:['Фиксация результата',state.expired?'Время вышло':'Разберём попытку','На общем экране выберите исход и угадавшего. Исправлять результат можно до подтверждения.'],board:['Ход подтверждён','Фишки передвинулись',state.last_transaction?previewText(state.last_transaction):''],finished:['Финиш!',state.winners.length>1?'Совместная победа!':`Победили: ${state.winners.map(sideName).join(', ')}`,state.winners.map(sideName).join(' · ')]};
      const t=titles[phase];text('phase-label',t[0]);text('turn-title',t[1]);text('turn-message',t[2]);
      text('accuracy',state.settings?`Точность ответа: ${state.settings.precision}`:'Сначала договоритесь о точности ответов.');
      $('method-badge').hidden=!method;text('method-badge',method?`${method.icon} ${method.label} · ${state.open_round?'открытый раунд':`+${state.difficulty}`}`:'');
      $('recovery').hidden=!recovery;
      if(host&&phase==='lobby')renderSetup();
      renderSecret();renderControls();renderBoard();renderRoster();renderHistory();
      $('invite').hidden=!host||phase!=='lobby';if(host&&phase==='lobby'){if(!$('qr').getAttribute('src'))$('qr').src=`${base}/qr.svg`;text('invite-code',code);$('invite-link').textContent=state.join_url.replace(/^https?:\/\//,'');$('invite-link').href=state.join_url;}
    }
    renderSketch();updateClock();
  }
  function renderRoster(){
    $('players').replaceChildren();
    if(state.sides.length)state.sides.forEach((side,i)=>{const group=el('div',undefined,'activity-side');group.style.setProperty('--side-color',side.color);group.append(el('strong',`${i+1}${side.symbol} ${side.name}${state.positions[i]!==undefined?` · ${state.positions[i]===49?'Финиш':state.positions[i]}`:''}`),el('p',side.members.map(playerName).join(', ')));$('players').append(group);});
    else state.players.forEach((player,i)=>{const row=el('div',player.name);if(host&&state.phase==='lobby'){const remove=button('×','remove',{target:i});remove.setAttribute('aria-label',`Убрать игрока ${player.name}`);row.append(remove);}$('players').append(row);});
  }
  function renderHistory(){
    $('history').replaceChildren();state.history.forEach(event=>{const li=el('li',`Ход ${event.number}. ${playerName(event.presenter)} · ${event.task}`);li.append(el('small',`${event.open?'Открытый раунд · ':''}${event.outcome==='success'?'Угадали':event.outcome==='violation'?'Нарушение':'Не угадали'}. ${previewText(event.transaction)}`));$('history').append(li);});
  }
  function updateClock(){if(!state)return;const deadline=state.phase==='performing'?state.deadline:state.phase==='memorize'?state.memorize_deadline:null;$('timer').hidden=!deadline;if(deadline){const n=seconds(deadline);text('timer',`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`);if(!n&&state.phase==='performing'&&beepTurn!==state.turn_id){beepTurn=state.turn_id;beep();poll(true);}}}
  setInterval(updateClock,200);

  function strokePath(stroke){const points=stroke.points;if(!points.length)return;ctx.strokeStyle=stroke.tool==='eraser'?'#fff':'#26344a';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=stroke.width;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(points[0][0]*1000,points[0][1]*620);for(const point of points.slice(1))ctx.lineTo(point[0]*1000,point[1]*620);if(points.length===1){ctx.arc(points[0][0]*1000,points[0][1]*620,stroke.width/2,0,Math.PI*2);ctx.fill();}else ctx.stroke();}
  function renderSketch(){if(!state)return;ctx.fillStyle='#fff';ctx.fillRect(0,0,1000,620);for(const stroke of state.drawing.strokes)strokePath(stroke);for(const [id,stroke] of optimistic){const remote=state.drawing.strokes.find(s=>s.id===id);if(stroke.complete&&remote&&remote.seq>=stroke.seq-1&&!inkQueue.some(op=>op.stroke_id===id)){optimistic.delete(id);continue;}strokePath(stroke);}}
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
    }catch(error){text('connection','Переподключаемся…');$('connection').className='connection-lost';feedback(error.name==='AbortError'?'Нет ответа от сервера. Восстанавливаем связь…':error.message);if(error.message.includes('повреждено')){$('corrupt-panel').hidden=false;$('room').hidden=true;}if(error.message==='Подключитесь к комнате заново.'){token=null;localStorage.removeItem(storageKey);$('room').hidden=true;if(!host)$('join-panel').hidden=false;}}
    finally{polling=false;if(token)timer=setTimeout(poll,state?.phase==='performing'&&category()?.method==='draw'?250:900);}}
  $('join-form').addEventListener('submit',async event=>{event.preventDefault();unlockAudio();feedback();$('join-submit').disabled=true;try{const result=await api(`${base}/join`,{name:$('player-name').value.trim()});localStorage.setItem(storageKey,result.token);token=result.token;await poll(true);}catch(error){feedback(error.name==='AbortError'?'Нет связи с сервером.':error.message);}finally{$('join-submit').disabled=false;}});
  $('reveal-secret').addEventListener('click',()=>{secretOpen=true;hasSeen=true;render(true);});$('hide-secret').addEventListener('click',()=>{secretOpen=false;render(true);});
  $('continue-recovery').addEventListener('click',()=>{recovery=false;render(true);});
  $('confirm-cancel').addEventListener('click',()=>$('confirm-dialog').close());$('confirm-accept').addEventListener('click',()=>{$('confirm-dialog').close();const callback=confirmCallback;confirmCallback=null;callback?.();});
  $('help-button').addEventListener('click',()=>$('help-dialog').showModal());$('sound-button').addEventListener('click',()=>{sound=!sound;$('sound-button').setAttribute('aria-pressed',String(sound));text('sound-button',sound?'Звук включён':'Звук выключен');if(sound)unlockAudio();});
  if(document.fullscreenEnabled&&host){$('fullscreen').hidden=false;$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{feedback('Включите полный экран через меню браузера.');}});}
  window.addEventListener('online',()=>{processInk();poll(true);});window.addEventListener('resize',()=>{layoutKey='';if(state)renderBoard();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){secretOpen=false;if(state)renderSecret();flushStroke();}else poll(true);});
  if(token){$('room').hidden=false;poll(true);}else if(!host)$('join-panel').hidden=false;else feedback('Создайте комнату в игротеке на этом устройстве.');
})();
