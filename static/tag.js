(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const mode = document.body.dataset.mode;
  const host = mode === 'host';
  const code = document.body.dataset.code;
  const storageKey = `tag:${mode}:${code}`;
  let token;
  try { token = localStorage.getItem(storageKey); } catch { /* Joining reports unavailable storage. */ }
  let state = null, previous = null, receivedAt = 0;
  let busy = false, polling = false, timer, socket = null, socketMatch = null;
  let superseded = false, signature = '', lastTag = -1;
  let input = {x:0,y:0}, pointer = null, anchor = null;
  const keys = new Set();
  const base = `/api/games/rooms/${encodeURIComponent(code)}`;
  const canvas = $('arena'), ctx = canvas.getContext('2d');
  const feedback = (text='') => { $('feedback').textContent=text; };
  const setText = (id,text) => { if($(id).textContent!==text) $(id).textContent=text; };
  const clock = (seconds) => { const n=Math.max(0,Math.ceil(seconds)); return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`; };

  async function api(path,data) {
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),8000);
    try {
      const response=await fetch(path,{method:data?'POST':'GET',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},
        ...(data?{body:JSON.stringify(data)}:{})});
      const result=await response.json(); if(!response.ok) throw new Error(result.error||'Не удалось выполнить запрос.'); return result;
    } finally { clearTimeout(timeout); }
  }
  function node(tag,text,className) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text;if(className)e.className=className;return e; }
  function button(text,action,extra={},primary=false) { const b=node('button',text,primary?'durak-primary':'durak-secondary');b.type='button';b.disabled=busy;b.addEventListener('click',()=>send(action,extra));return b; }
  function colorAvatar(e,player) { e.textContent=player.name.slice(0,1).toUpperCase(); e.style.backgroundColor=player.color; }
  async function send(action,extra={}) {
    if(busy||!state)return;busy=true;feedback();render(true);
    try { await api(`${base}/action`,{action,revision:state.revision,...extra}); }
    catch(error) { feedback(error.name==='AbortError'?'Нет ответа от сервера. Переподключаемся…':error.message); }
    finally { busy=false;await poll(true); }
  }

  function receive(next,live=false) {
    if(next.game!=='tag')throw new Error('Откройте эту комнату через игротеку.');
    if(state && (state.match!==next.match||next.phase!=='playing')) stopInput();
    if(live) { previous=state;receivedAt=performance.now(); }
    state={...state,...next};
    render();
    if(state.phase==='playing') connect(); else disconnect();
  }
  function render(force=false) {
    if(!state)return;
    const phase=state.phase, playing=phase==='playing', lobby=phase==='lobby';
    $('join-panel').hidden=true;$('room').hidden=false;
    document.body.classList.toggle('is-playing',playing);
    setText('room-code',`Комната ${code} · ${state.players.length}/8 игроков`);
    $('stage-panel').hidden=!host&&phase!=='finished';$('phone-lobby').hidden=host||!lobby;$('controller').hidden=host||!playing;
    $('timer').hidden=!playing;
    setText('phase-label',lobby?'Лови, касайся, убегай':playing?'Догонялки на общем экране':'Раунд завершён');
    setText('stage-title',lobby?'Все на арену!':playing?'Кто сегодня Голя?':'Отлично побегали!');
    setText('stage-message',lobby?'Подключите от 2 до 8 игроков. Каждый получит свой цвет.':playing?`Голя — ${state.players[state.tagger].name}. Коснись другого игрока, чтобы передать роль.`:'Результаты раунда — время в роли Голи и успешные касания.');
    $('arena-wrap').hidden=phase==='finished';$('results').hidden=phase!=='finished';
    if(playing) {
      setText('timer',clock(state.duration-state.elapsed));setText('phone-timer',clock(state.duration-state.elapsed));
      const live=socket?.readyState===WebSocket.OPEN;
      $('arena-overlay').hidden=!state.paused&&state.countdown<=0&&live;
      setText('overlay-title',!live?'Подключаемся…':state.paused?'Пауза':String(Math.ceil(state.countdown)));
      setText('overlay-message',!live?'Восстанавливаем игровое соединение':state.paused?state.pause_reason:'Приготовьтесь бежать!');
    } else $('arena-overlay').hidden=true;
    if(!host&&state.you!==null&&state.players[state.you]) {
      const own=state.players[state.you];
      colorAvatar($('own-avatar'),own);colorAvatar($('lobby-avatar'),own);
      $('joystick-knob').style.backgroundColor=own.color;
      setText('lobby-name',own.name);setText('controller-title',own.name);
      setText('own-role',playing&&state.tagger===state.you?'Вы — Голя!':'Убегайте от Голи');
      setText('controller-note',state.paused?state.pause_reason:state.countdown>0?'Скоро старт! Найдите своего человечка на телевизоре.':state.tagger===state.you?'Догоните и коснитесь любого другого игрока.':'Не дайте себя поймать. Управляйте пальцем ниже.');
    }
    const key=JSON.stringify([phase,state.match,state.revision,state.tagger,state.players.map(p=>[p.name,p.color,playing?p.connected:p.online]),busy]);
    if(!force&&signature===key)return;signature=key;
    $('players').replaceChildren();
    state.players.forEach((player,index)=>{
      const e=node('div',undefined,`durak-player${playing&&state.tagger===index?' tag-player-chaser':''}`);
      const avatar=node('span',undefined,'player-avatar tag-color-avatar');colorAvatar(avatar,player);
      const text=node('div');text.append(node('strong',player.name));
      text.append(node('small',playing?(player.connected?'На связи':'Ждём подключения'):lobby?'Готов к игре':`${player.seconds.toFixed(1)} сек. Голей`));
      e.append(avatar,text);
      if(host&&lobby){const remove=button('×','remove',{target:index});remove.className='remove-player';remove.setAttribute('aria-label',`Убрать игрока ${player.name}`);e.append(remove);}
      $('players').append(e);
    });
    $('invite').hidden=!host||!lobby;
    if(host&&lobby){if(!$('qr').getAttribute('src'))$('qr').src=`${base}/qr.svg`;setText('invite-code',code);$('invite-link').textContent=state.join_url.replace(/^https?:\/\//,'');$('invite-link').href=state.join_url;}
    $('host-actions').replaceChildren();
    if(host){
      if(lobby||phase==='finished'){const start=button(phase==='finished'?'Ещё раунд →':'Начать игру →','start',{},true);start.disabled=busy||state.players.length<2;$('host-actions').append(start);}
      if(playing)$('host-actions').append(button('Завершить раунд','stop'));
      if(!lobby){const reset=node('button','Вернуться в лобби','durak-secondary');reset.type='button';reset.disabled=busy;reset.addEventListener('click',()=>$('confirm-dialog').showModal());$('host-actions').append(reset);}
    }
    if(phase==='finished'){
      setText('results-title',state.winners.length===state.players.length?'Победила вся компания!':`Лучше всех убегали: ${state.winners.map(i=>state.players[i].name).join(', ')}`);
      $('scoreboard').replaceChildren();
      [...state.players].sort((a,b)=>a.seconds-b.seconds).forEach(player=>{const li=node('li');li.append(node('strong',player.name),node('span',`Голей: ${player.seconds.toFixed(1)} сек. · Касаний: ${player.catches}`));$('scoreboard').append(li);});
    }
    if(state.tag_version!==lastTag){lastTag=state.tag_version;}
  }

  function connect() {
    if(!token||document.hidden||superseded||state?.phase!=='playing')return;
    if(socket&&socketMatch===state.match&&[WebSocket.OPEN,WebSocket.CONNECTING].includes(socket.readyState))return;
    disconnect();socketMatch=state.match;
    const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws/games/tag/${encodeURIComponent(code)}`);socket=ws;
    ws.addEventListener('open',()=>{if(socket!==ws)return;ws.send(JSON.stringify({type:'auth',token,match:socketMatch}));sendInput();});
    ws.addEventListener('message',(event)=>{
      if(socket!==ws)return;
      try{const message=JSON.parse(event.data);if(message.type==='error'){feedback(message.error);if(message.error==='Управление открыто на другом экране.')superseded=true;}
        else if(message.type==='state'){setText('connection','● В игре');$('connection').className='connection-ok';receive(message.state,true);}}
      catch{feedback('Не удалось обновить арену. Переподключаемся.');}
    });
    ws.addEventListener('close',()=>{if(socket!==ws)return;socket=null;stopInput();setText('connection','Переподключаемся…');$('connection').className='connection-lost';});
    ws.addEventListener('error',()=>{if(socket===ws)setText('connection','Проверяем соединение…');});
  }
  function disconnect(){const old=socket;socket=null;socketMatch=null;if(old)old.close();}
  function sendInput(){if(socket?.readyState!==WebSocket.OPEN||state?.phase!=='playing')return;socket.send(JSON.stringify(host?{type:'ping'}:{type:'input',match:state.match,...input}));}
  let pulse=0;setInterval(()=>{if(host){if(++pulse%10===0)sendInput();}else sendInput();},50);

  const pad=$('touch-pad');
  function stopInput(){input={x:0,y:0};keys.clear();pointer=null;anchor=null;pad.classList.remove('is-dragging');$('joystick-base').hidden=true;$('joystick-knob').hidden=true;sendInput();}
  function movePointer(event){
    if(pointer!==event.pointerId||!anchor)return;
    const rect=pad.getBoundingClientRect(),dx=event.clientX-rect.left-anchor.x,dy=event.clientY-rect.top-anchor.y;
    const distance=Math.hypot(dx,dy),scale=Math.max(64,distance);
    input=distance<5?{x:0,y:0}:{x:dx/scale,y:dy/scale};
    $('joystick-knob').style.left=`${anchor.x+input.x*54}px`;$('joystick-knob').style.top=`${anchor.y+input.y*54}px`;
  }
  pad.addEventListener('pointerdown',(event)=>{if(host||state?.phase!=='playing'||pointer!==null)return;event.preventDefault();pad.focus({preventScroll:true});const rect=pad.getBoundingClientRect();pointer=event.pointerId;anchor={x:event.clientX-rect.left,y:event.clientY-rect.top};pad.setPointerCapture(pointer);pad.classList.add('is-dragging');$('joystick-base').hidden=false;$('joystick-knob').hidden=false;for(const id of ['joystick-base','joystick-knob']){$(id).style.left=`${anchor.x}px`;$(id).style.top=`${anchor.y}px`;}movePointer(event);});
  pad.addEventListener('pointermove',(event)=>{if(pointer===event.pointerId){event.preventDefault();movePointer(event);}});
  for(const type of ['pointerup','pointercancel','lostpointercapture'])pad.addEventListener(type,(event)=>{if(pointer===event.pointerId)stopInput();});
  const movementKeys=new Set(['arrowup','arrowdown','arrowleft','arrowright','w','a','s','d']);
  function keyboard(){let x=Number(keys.has('arrowright')||keys.has('d'))-Number(keys.has('arrowleft')||keys.has('a'));let y=Number(keys.has('arrowdown')||keys.has('s'))-Number(keys.has('arrowup')||keys.has('w'));const length=Math.max(1,Math.hypot(x,y));input={x:x/length,y:y/length};sendInput();}
  window.addEventListener('keydown',(event)=>{const key=event.key.toLowerCase();if(!host&&state?.phase==='playing'&&movementKeys.has(key)&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();keys.add(key);keyboard();}});
  window.addEventListener('keyup',(event)=>{const key=event.key.toLowerCase();if(movementKeys.has(key)){keys.delete(key);keyboard();}});
  window.addEventListener('blur',stopInput);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopInput();disconnect();}else poll(true);});

  function roundRect(x,y,w,h,r,fill){ctx.fillStyle=fill;ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fill();}
  function person(player,x,y,index,now,moving){
    ctx.save();ctx.translate(x,y);ctx.globalAlpha=state.phase==='playing'&&!player.connected?0.5:1;
    ctx.fillStyle='#58744324';ctx.beginPath();ctx.ellipse(0,30,24,8,0,0,Math.PI*2);ctx.fill();
    if(player.protected){ctx.setLineDash([5,5]);ctx.strokeStyle='#fff';ctx.lineWidth=3;ctx.beginPath();ctx.arc(0,5,33,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);}
    if(index===state.tagger&&state.phase==='playing'){ctx.strokeStyle='#f3bd34';ctx.lineWidth=4;ctx.beginPath();ctx.arc(0,4,35+Math.sin(now/150)*2,0,Math.PI*2);ctx.stroke();}
    const walk=moving?Math.sin(now/100+index)*6:0;
    ctx.lineCap='round';ctx.strokeStyle='#364758';ctx.lineWidth=8;
    for(const side of [-1,1]){ctx.beginPath();ctx.moveTo(side*7,14);ctx.lineTo(side*11,30+walk*side);ctx.stroke();}
    ctx.strokeStyle=player.color;ctx.lineWidth=8;
    for(const side of [-1,1]){ctx.beginPath();ctx.moveTo(side*10,-4);ctx.lineTo(side*22,8-walk*side);ctx.stroke();}
    roundRect(-14,-12,28,34,11,player.color);
    ctx.fillStyle='#ffe4c5';ctx.beginPath();ctx.arc(0,-24,12,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#354354';for(const side of [-1,1]){ctx.beginPath();ctx.arc(side*4,-24,1.4,0,Math.PI*2);ctx.fill();}
    ctx.font='700 15px sans-serif';const name=player.name.length>15?player.name.slice(0,14)+'…':player.name;const width=ctx.measureText(name).width+18;
    const nameX=Math.max(8-x,Math.min(-width/2,1200-width-8-x));
    roundRect(nameX,42,width,25,9,'#ffffffdd');ctx.fillStyle='#344452';ctx.textAlign='center';ctx.fillText(name,nameX+width/2,59);
    if(index===state.tagger&&state.phase==='playing'){const labelX=Math.max(8-x,Math.min(-39,1114-x)),labelY=Math.max(8-y,-76);roundRect(labelX,labelY,78,29,12,'#ffc943');ctx.font='800 20px sans-serif';ctx.fillStyle='#65410c';ctx.fillText('Голя',labelX+39,labelY+22);}
    ctx.restore();
  }
  function draw(now){
    requestAnimationFrame(draw);if(!state||!host||state.phase==='finished'||document.hidden)return;
    ctx.clearRect(0,0,1200,720);roundRect(0,0,1200,720,0,'#e8efdf');
    ctx.strokeStyle='#cad8bd';ctx.lineWidth=1;for(let x=0;x<1200;x+=60){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,720);ctx.stroke();}for(let y=0;y<720;y+=60){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(1200,y);ctx.stroke();}
    ctx.strokeStyle='#b8cda8';ctx.lineWidth=4;ctx.beginPath();ctx.roundRect(10,10,1180,700,20);ctx.stroke();
    ctx.strokeStyle='#d2dfc7';ctx.lineWidth=3;ctx.beginPath();ctx.arc(600,360,110,0,Math.PI*2);ctx.stroke();
    const fraction=Math.min(1,Math.max(0,(now-receivedAt)/50));
    state.players.forEach((player,index)=>{
      const count=state.players.length;
      const current=player.x===null?{x:600+260*Math.cos(index*2*Math.PI/count),y:360+210*Math.sin(index*2*Math.PI/count)}:player;
      const old=previous?.match===state.match&&previous?.phase===state.phase?previous.players[index]:null;
      const x=old?.x!=null?old.x+(current.x-old.x)*fraction:current.x,y=old?.y!=null?old.y+(current.y-old.y)*fraction:current.y;
      const moving=old?.x!=null&&Math.hypot(current.x-old.x,current.y-old.y)>0.1&&!state.paused;
      person(player,x,y,index,now,moving);
    });
  }
  requestAnimationFrame(draw);

  async function poll(force=false){
    clearTimeout(timer);if(!token||polling)return;polling=true;
    try{const next=await api(base);if(force)signature='';
      if(next.phase!=='playing'||socket?.readyState!==WebSocket.OPEN||!state)receive(next);else if(state.match!==next.match)receive(next);
      if(next.phase!=='playing'){setText('connection','● На связи');$('connection').className='connection-ok';}
    }catch(error){feedback(error.name==='AbortError'?'Нет ответа от сервера. Восстанавливаем связь…':error.message);setText('connection','Переподключаемся…');$('connection').className='connection-lost';
      if(error.message==='Подключитесь к комнате заново.'){token=null;localStorage.removeItem(storageKey);disconnect();$('room').hidden=true;if(!host)$('join-panel').hidden=false;}}
    finally{polling=false;if(token)timer=setTimeout(poll,1000);}
  }
  $('join-form').addEventListener('submit',async(event)=>{event.preventDefault();feedback();$('join-submit').disabled=true;try{const result=await api(`${base}/join`,{name:$('player-name').value.trim()});localStorage.setItem(storageKey,result.token);token=result.token;await poll(true);}catch(error){feedback(error.name==='AbortError'?'Нет связи с сервером.':error.message);}finally{$('join-submit').disabled=false;}});
  $('cancel-reset').addEventListener('click',()=>$('confirm-dialog').close());$('confirm-reset').addEventListener('click',()=>{$('confirm-dialog').close();send('reset');});
  window.addEventListener('online',()=>poll(true));
  if(document.fullscreenEnabled&&host){$('fullscreen').hidden=false;$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{feedback('Включите полный экран через меню браузера.');}});}
  if(token){$('room').hidden=false;poll(true);}else if(!host)$('join-panel').hidden=false;else feedback('Создайте комнату в игротеке на этом устройстве.');
})();
