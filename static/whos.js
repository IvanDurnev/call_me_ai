(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const mode = document.body.dataset.mode;
  const code = document.body.dataset.code;
  const storageKey = `whos:${mode}:${code}`;
  let token;
  try { token = localStorage.getItem(storageKey); } catch { /* The join form explains storage errors. */ }
  let state = null;
  let busy = false;
  let polling = false;
  let timer;
  let rendered = -1;
  let forceRender = false;
  let reconnecting = false;
  let selected;
  let questionKey = '';
  const base = `/api/games/rooms/${encodeURIComponent(code)}`;
  const feedback = (text = '') => { $('feedback').textContent = text; };
  const playerName = (index) => state.players[index]?.name || '';

  async function api(path, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {
        method: data ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})},
        ...(data ? {body: JSON.stringify(data)} : {}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось выполнить запрос.');
      return result;
    } finally { clearTimeout(timeout); }
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function actionButton(label, action, extra = {}, primary = false) {
    const button = element('button', label, primary ? 'durak-primary' : 'durak-secondary');
    button.type = 'button'; button.disabled = busy;
    button.addEventListener('click', () => send(action, extra));
    return button;
  }

  async function send(action, extra = {}) {
    if (busy || !state) return;
    busy = true; feedback(); render();
    try {
      await api(`${base}/action`, {action, revision: state.revision, phase: state.phase,
        match: state.match, round: state.round, ...extra});
    } catch (error) {
      feedback(error.name === 'AbortError' ? 'Нет ответа от сервера. Проверяем, принят ли ответ…' : error.message);
    } finally { busy = false; await poll(true); }
  }

  function renderBallot() {
    const canVote = mode === 'player' && state.phase === 'voting' && !state.has_voted;
    $('ballot').hidden = !canVote;
    $('choices').replaceChildren(); $('ballot-actions').replaceChildren();
    if (canVote) {
      state.players.forEach((player, index) => {
        const choice = element('button', undefined, `whos-choice${selected === index ? ' selected-choice' : ''}`);
        choice.type = 'button'; choice.disabled = busy;
        choice.setAttribute('aria-pressed', String(selected === index));
        choice.setAttribute('aria-label', player.name);
        choice.append(element('span', player.name.slice(0,1).toUpperCase(), 'player-avatar'), element('strong', player.name));
        if (index === state.you) choice.append(element('small', 'Это вы'));
        choice.addEventListener('click', () => { selected = index; renderBallot(); });
        $('choices').append(choice);
      });
      const confirm = actionButton('Подтвердить выбор', 'vote', {target:selected}, true);
      confirm.disabled = busy || selected === undefined;
      $('ballot-actions').append(confirm, actionButton('Пропустить вопрос', 'vote', {target:null}));
    }
    $('vote-status').hidden = !(mode === 'player' && state.phase === 'voting' && state.has_voted);
    $('vote-status').textContent = state.has_voted ? (state.own_vote === null ? 'Вы пропустили вопрос. Ждём остальных.' : `Вы выбрали: ${playerName(state.own_vote)}. Ждём остальных.`) : '';
  }

  function renderResults() {
    $('results').hidden = state.phase !== 'results';
    $('bars').replaceChildren(); $('leaders').replaceChildren();
    if (state.phase === 'results') {
      const result = state.result;
      $('results-title').textContent = result.leaders.length ? (result.leaders.length > 1 ? 'Компания выбрала нескольких!' : 'Компания решила:') : 'В этот раз без номинации';
      result.leaders.forEach((index) => $('leaders').append(element('span', `✦ ${playerName(index)}`, 'whos-leader')));
      const maximum = Math.max(1, ...result.counts);
      const order = state.players.map((_,index) => index).sort((a,b) => result.counts[b]-result.counts[a]);
      for (const index of order) {
        const bar = element('div', undefined, 'whos-bar');
        const label = element('div', undefined, 'whos-bar-label');
        label.append(element('strong', playerName(index)), element('span', `${result.counts[index]}`));
        const track = element('div', undefined, 'whos-bar-track');
        const fill = element('span'); fill.style.width = `${result.counts[index]/maximum*100}%`;
        track.append(fill); bar.append(label, track); $('bars').append(bar);
      }
      $('result-note').textContent = `Голосов за игроков: ${result.counts.reduce((a,b)=>a+b,0)} · Без ответа или пропуск: ${result.skipped}. Кто за кого голосовал — остаётся секретом.`;
    }
    $('summary').hidden = state.phase !== 'finished';
    $('awards').replaceChildren(); $('history').replaceChildren();
    if (state.phase === 'finished') {
      const order = state.players.map((_,index)=>index).sort((a,b) => state.nominations[b]-state.nominations[a] || state.totals[b]-state.totals[a]);
      for (const index of order) {
        const item = element('li');
        item.append(element('span', playerName(index)), element('small', `Номинаций: ${state.nominations[index]} · Голосов: ${state.totals[index]}`));
        $('awards').append(item);
      }
      state.history.forEach((result) => {
        const item = element('li');
        item.append(element('strong',result.question), element('p',result.leaders.length ? result.leaders.map(playerName).join(', ') : 'Без номинации'));
        $('history').append(item);
      });
    }
  }

  function render() {
    if (!state) return;
    const host = mode === 'host';
    const {phase} = state;
    const key = `${state.match}:${state.round}`;
    if (questionKey !== key) { selected = undefined; questionKey = key; }
    $('join-panel').hidden = true; $('room').hidden = false;
    $('room-code').textContent = `Комната ${code} · ${state.players.length}/12 игроков`;
    $('phase-label').textContent = phase === 'lobby' ? 'Узнаем друг друга поближе' : phase === 'finished' ? 'Партия завершена' : `Вопрос ${state.round} из ${state.round_count}`;
    $('question-title').textContent = phase === 'lobby' ? 'Собираемся играть' : phase === 'finished' ? 'Вот такая у нас компания!' : state.question;
    $('stage-message').textContent = phase === 'lobby' ? 'Подключите от 3 до 12 игроков с телефонов.' : phase === 'voting' ? `Ответили ${state.vote_count} из ${state.players.length}. Выберите на телефоне того, кому подходит вопрос. Можно выбрать себя.` : phase === 'results' ? 'Обсудите результат — и переходите к следующему вопросу.' : '10 вопросов, неожиданные совпадения и новые поводы улыбнуться.';
    $('lobby-intro').hidden = phase !== 'lobby';
    renderBallot(); renderResults();
    $('players').replaceChildren();
    state.players.forEach((player,index) => {
      const item = element('div',undefined,`durak-player${index === state.you ? ' is-you' : ''}`);
      const text = element('div'); text.append(element('strong', player.name));
      text.append(element('small',phase === 'voting' ? (player.voted ? 'Ответ принят ✓' : 'Выбирает') : phase === 'lobby' ? 'Готов к игре' : 'В компании'));
      const online = element('small',player.online ? 'На связи' : 'Нет связи — ждём',player.online ? 'player-online' : 'player-offline');
      online.dataset.online = String(index); text.append(online);
      item.append(element('span',player.name.slice(0,1).toUpperCase(),'player-avatar'),text);
      if (host && phase === 'lobby') {
        const remove = actionButton('×','remove',{target:index}); remove.className = 'remove-player'; remove.setAttribute('aria-label',`Убрать игрока ${player.name}`); item.append(remove);
      }
      $('players').append(item);
    });
    $('invite').hidden = !host || phase !== 'lobby';
    if (host && phase === 'lobby') {
      if (!$('qr').getAttribute('src')) $('qr').src = `${base}/qr.svg`;
      $('invite-code').textContent = code; $('invite-link').textContent = state.join_url.replace(/^https?:\/\//,''); $('invite-link').href = state.join_url;
    }
    $('host-actions').replaceChildren();
    if (host) {
      const actions = $('host-actions');
      if (phase === 'lobby' || phase === 'finished') {
        const start = actionButton(phase === 'finished' ? 'Сыграть ещё раз →' : 'Начать игру →','start',{},true);
        start.disabled = busy || state.players.length < 3; actions.append(start);
      }
      if (phase === 'voting') {
        const reveal = actionButton('Показать результаты','reveal',{},true); reveal.disabled = busy || !state.vote_count;
        actions.append(reveal,element('p','Если кто-то не отвечает, можно раскрыть уже принятые голоса.','durak-muted'));
      }
      if (phase === 'results') actions.append(actionButton(state.round === state.round_count ? 'Итоги игры →' : 'Следующий вопрос →','next',{},true));
      if (phase !== 'lobby') {
        const reset = element('button','Вернуться в лобби','durak-secondary'); reset.type='button'; reset.disabled=busy;
        reset.addEventListener('click',()=>$('confirm-dialog').showModal()); actions.append(reset);
      }
    }
  }

  async function poll(force = false) {
    clearTimeout(timer); if (force) forceRender = true;
    if (!token || polling) return;
    polling = true;
    try {
      const next = await api(base);
      if (next.game !== 'whos') throw new Error('Это комната другой игры. Откройте её через игротеку.');
      state = next;
      if (reconnecting) feedback(); reconnecting=false;
      $('connection').textContent='● На связи'; $('connection').className='connection-ok';
      if (forceRender || rendered !== state.revision) { render(); rendered=state.revision; forceRender=false; }
      else state.players.forEach((player,index)=>{
        const node=document.querySelector(`[data-online="${index}"]`);
        if (node) { node.textContent=player.online?'На связи':'Нет связи — ждём'; node.className=player.online?'player-online':'player-offline'; }
      });
    } catch(error) {
      reconnecting=true; $('connection').textContent='Переподключаемся…'; $('connection').className='connection-lost';
      feedback(error.name==='AbortError'?'Нет ответа от сервера. Восстанавливаем связь…':error.message);
      if(error.message==='Подключитесь к комнате заново.') {
        token=null; localStorage.removeItem(storageKey); $('room').hidden=true;
        if(mode==='player') $('join-panel').hidden=false;
      }
    } finally { polling=false; if(token) timer=setTimeout(poll,1000); }
  }

  $('join-form').addEventListener('submit',async(event)=>{
    event.preventDefault(); feedback(); $('join-submit').disabled=true;
    try {
      const result=await api(`${base}/join`,{name:$('player-name').value.trim()});
      localStorage.setItem(storageKey,result.token); token=result.token; await poll(true);
    } catch(error) { feedback(error.name==='AbortError'?'Нет связи с сервером.':error.message); }
    finally { $('join-submit').disabled=false; }
  });
  $('cancel-reset').addEventListener('click',()=>$('confirm-dialog').close());
  $('confirm-reset').addEventListener('click',()=>{ $('confirm-dialog').close(); send('reset'); });
  window.addEventListener('online',()=>poll(true));
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden) poll(true); });
  if(document.fullscreenEnabled && mode==='host') {
    $('fullscreen').hidden=false;
    $('fullscreen').addEventListener('click',async()=>{
      try { if(document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
      catch { feedback('Включите полный экран через меню браузера.'); }
    });
  }
  if(token) { $('room').hidden=false; poll(true); }
  else if(mode==='player') $('join-panel').hidden=false;
  else feedback('Создайте комнату в игротеке на этом устройстве.');
})();
