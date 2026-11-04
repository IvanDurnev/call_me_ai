(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const mode = document.body.dataset.mode;
  let code = document.body.dataset.code;
  let token = code ? localStorage.getItem(`durak:${mode}:${code}`) : null;
  let state = null;
  let selected = null;
  let target = null;
  let busy = false;
  let timer;
  let lastRender = -1;
  let polling = false;
  let forceRender = false;
  let connectionFailed = false;
  const suits = {S: '♠', H: '♥', D: '♦', C: '♣'};
  const ranks = {11: 'В', 12: 'Д', 13: 'К', 14: 'Т'};
  const cardRank = (card) => Number(card.slice(1));
  const cardName = (card) => `${ranks[cardRank(card)] || cardRank(card)}${suits[card[0]]}`;
  const feedback = (message = '') => { $('feedback').textContent = message; };
  const endpoint = () => `/api/games/rooms/${encodeURIComponent(code)}`;

  async function api(path, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {
        method: data ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})},
        ...(data ? {body: JSON.stringify(data)} : {}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Не удалось выполнить запрос.');
      return body;
    } finally { clearTimeout(timeout); }
  }

  function cardElement(card, interactive = false) {
    const node = document.createElement(interactive ? 'button' : 'div');
    if (interactive) node.type = 'button';
    node.className = `durak-card${'HD'.includes(card[0]) ? ' red-card' : ''}`;
    node.setAttribute('aria-label', cardName(card));
    const corner = document.createElement('span');
    corner.className = 'durak-card-corner';
    corner.textContent = cardName(card);
    const center = document.createElement('span');
    center.className = 'durak-card-suit';
    center.textContent = suits[card[0]];
    node.append(corner, center);
    return node;
  }

  function button(text, action, className = 'durak-secondary', extra = {}) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = className; node.textContent = text;
    node.disabled = busy;
    node.addEventListener('click', () => send(action, extra));
    return node;
  }

  async function send(action, extra = {}) {
    if (busy || !state) return;
    busy = true; feedback(); render();
    try {
      await api(`${endpoint()}/action`, {action, revision: state.revision, ...extra});
      selected = null; target = null;
    } catch (error) {
      feedback(error.name === 'AbortError' ? 'Связь прервалась. Проверяем, прошёл ли ход…' : error.message);
    } finally {
      busy = false;
      await poll(true);
    }
  }

  function canBeat(card, attack) {
    return (card[0] === attack[0] && cardRank(card) > cardRank(attack)) ||
      (card[0] === state.trump[0] && attack[0] !== state.trump[0]);
  }

  function renderHand() {
    $('hand-panel').hidden = mode !== 'player';
    if (mode !== 'player') return;
    const you = state.you;
    const finished = state.winners.includes(you);
    const defending = you === state.defender;
    const playing = state.phase === 'playing' && !finished;
    const uncovered = state.table.map((pair, index) => pair[1] ? null : index).filter((index) => index !== null);
    if (!uncovered.includes(target)) target = uncovered[0] ?? null;
    if (!state.hand.includes(selected)) selected = null;
    $('hand-title').textContent = `${state.players[you].name} · ваши карты`;
    $('your-role').textContent = finished ? 'Вы вышли!' : playing ? (defending ? 'Вы отбиваетесь' : you === state.attacker ? 'Вы ходите' : 'Можно подкинуть') : '';
    const allCovered = state.table.length > 0 && uncovered.length === 0;
    const tableRanks = new Set(state.table.flat().filter(Boolean).map(cardRank));
    function playable(card) {
      if (!playing || busy) return false;
      if (defending) return !state.taking && target !== null && canBeat(card, state.table[target][0]);
      return state.table.length < state.limit &&
        (state.table.length ? tableRanks.has(cardRank(card)) : you === state.attacker);
    }
    $('hand').replaceChildren();
    const sorted = [...state.hand].sort((a, b) => {
      const trump = state.trump?.[0];
      return (a[0] === trump) - (b[0] === trump) || 'SHDC'.indexOf(a[0]) - 'SHDC'.indexOf(b[0]) || cardRank(a) - cardRank(b);
    });
    for (const card of sorted) {
      const node = cardElement(card, true);
      node.disabled = !playable(card);
      node.classList.toggle('selected-card', selected === card);
      node.setAttribute('aria-pressed', String(selected === card));
      node.addEventListener('click', () => { selected = selected === card ? null : card; render(); });
      $('hand').append(node);
    }
    $('player-actions').replaceChildren();
    let help = 'Ждём начала игры на общем экране.';
    if (state.phase === 'finished') help = 'Партия завершена. На общем экране можно начать реванш.';
    else if (finished) help = 'Вы избавились от всех карт. Наблюдайте за игрой!';
    else if (playing) {
      if (defending) {
        help = state.taking ? 'Вы берёте. Ждём, пока остальные закончат подкидывать.' :
          uncovered.length ? 'Выберите карту на столе, затем карту в руке, чтобы отбить её.' :
          state.table.length ? 'Всё отбито. Ждём решения остальных игроков.' : 'Соперник выбирает первую карту.';
        if (selected && playable(selected)) $('player-actions').append(button(`Отбить ${cardName(state.table[target][0])}`, 'defend', 'durak-primary', {card: selected, target}));
        if (state.table.length && !state.taking) $('player-actions').append(button('Беру карты', 'take'));
      } else {
        help = !state.table.length ? (you === state.attacker ? 'Выберите карту и начните ход.' : 'Ждём первой карты атакующего.') :
          state.taking ? 'Соперник берёт. Подкиньте или завершите ход.' :
          allCovered ? 'Подкиньте карту или нажмите «Бито».' : 'Можно подкинуть достоинство, которое уже есть на столе.';
        if (selected && playable(selected)) $('player-actions').append(button(state.table.length ? 'Подкинуть карту' : 'Ходить', 'attack', 'durak-primary', {card: selected}));
        if (state.table.length && (allCovered || state.taking)) {
          const passed = state.passed.includes(you);
          const pass = button(passed ? 'Ждём остальных…' : state.taking ? 'Больше не подкидываю' : 'Бито', 'pass');
          pass.disabled = busy || passed;
          $('player-actions').append(pass);
        }
      }
    }
    $('hand-help').textContent = help;
  }

  function render() {
    if (!state) return;
    $('room-panel').hidden = false;
    $('join-panel').hidden = true;
    $('room-label').textContent = `Комната ${code} · ${state.players.length}/6 игроков`;
    const lobby = state.phase === 'lobby';
    const finished = state.phase === 'finished';
    $('game-sidebar').hidden = lobby;
    document.querySelector('.table-panel').classList.toggle('active-table', !lobby);
    for (const [id, destination] of [['players', lobby ? 'lobby-roster' : 'game-roster'], ['host-controls', lobby ? 'lobby-controls' : 'game-controls']]) {
      if ($(id).parentElement !== $(destination)) $(destination).append($(id));
    }
    $('phase-label').hidden = lobby;
    $('phase-label').textContent = lobby ? '' : finished ? 'Партия завершена' : 'Играем вместе';
    $('table-title').textContent = lobby ? 'Собираемся за столом' : finished ? 'До следующей раздачи!' : 'Игровой стол';
    $('turn-message').hidden = finished;
    $('turn-message').textContent = finished ? '' : state.message;
    $('lobby-help').hidden = !lobby;
    $('players').replaceChildren();
    state.players.forEach((player, index) => {
      const item = document.createElement('div');
      item.className = `durak-player${index === state.you ? ' is-you' : ''}${!lobby && !finished && index === state.defender ? ' is-defender' : ''}`;
      const avatar = document.createElement('span'); avatar.className = 'player-avatar'; avatar.textContent = player.name.slice(0, 1).toUpperCase();
      const text = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = player.name;
      const status = document.createElement('small');
      status.textContent = player.finished ? 'Вышел из игры ✓' : lobby ? 'Готов к игре' : `${player.count} карт${index === state.defender && !finished ? ' · отбивается' : index === state.attacker && !finished ? ' · ходит' : ''}`;
      const online = document.createElement('small'); online.dataset.online = String(index);
      online.textContent = player.online ? 'На связи' : 'Нет связи — ждём';
      online.className = player.online ? 'player-online' : 'player-offline';
      text.append(name, status, online); item.append(avatar, text);
      if (mode === 'host' && lobby) {
        const remove = button('×', 'remove', 'remove-player', {target: index});
        remove.setAttribute('aria-label', `Убрать игрока ${player.name}`); item.append(remove);
      }
      $('players').append(item);
    });
    $('deck-label').hidden = lobby || !state.trump;
    $('deck-label').replaceChildren();
    if (state.trump && !lobby) {
      const trump = cardElement(state.trump);
      trump.classList.add('trump-card');
      const label = document.createElement('span'); label.textContent = `Козырь ${suits[state.trump[0]]} · В колоде: ${state.deck_count} · Отбой: ${state.discard}`;
      $('deck-label').append(trump, label);
    }
    $('table-cards').replaceChildren();
    state.table.forEach((pair, index) => {
      const cell = document.createElement('div'); cell.className = 'table-pair';
      const attack = cardElement(pair[0], mode === 'player' && state.you === state.defender && !pair[1] && !state.taking);
      if (attack.tagName === 'BUTTON') {
        attack.classList.toggle('target-card', target === index);
        attack.addEventListener('click', () => { target = index; selected = null; render(); });
      }
      cell.append(attack);
      if (pair[1]) { const defense = cardElement(pair[1]); defense.classList.add('defense-card'); cell.append(defense); }
      $('table-cards').append(cell);
    });
    $('result').hidden = !finished;
    $('result').textContent = finished ? (state.loser === null ? 'Все вышли — ничья!' : `${state.players[state.loser].name} проиграл`) : '';
    $('host-controls').hidden = mode !== 'host';
    $('start-game').textContent = finished ? 'Сыграть ещё раз' : lobby ? 'Начать игру →' : 'Вернуться в лобби';
    $('start-game').disabled = (lobby && state.players.length < 2) || busy;
    $('start-hint').textContent = !lobby && !finished ? 'Возврат в лобби завершит текущую партию.' : state.players.length < 2 ? 'Подключите хотя бы двух игроков с телефонов.' : 'Когда все готовы, можно начинать.';
    $('invite-panel').hidden = mode !== 'host' || !lobby;
    $('room-qr').hidden = false;
    $('invite-heading').textContent = lobby ? 'Заходите в игру' : 'Комната вашей компании';
    if (!$('room-qr').getAttribute('src')) $('room-qr').src = `${endpoint()}/qr.svg`;
    $('invite-code').textContent = code;
    $('invite-link').textContent = state.join_url.replace(/^https?:\/\//, '');
    $('invite-link').href = state.join_url;
    // Establish the target before rendering cards so the initial target is highlighted.
    if (!state.table[target] || state.table[target][1]) target = state.table.findIndex((pair) => !pair[1]);
    renderHand();
    document.querySelectorAll('#table-cards button').forEach((node, index) => {
      const uncovered = state.table.map((pair, i) => pair[1] ? null : i).filter((i) => i !== null);
      node.classList.toggle('target-card', uncovered[index] === target);
    });
  }

  async function poll(force = false) {
    clearTimeout(timer);
    if (force) forceRender = true;
    if (!token || polling) return;
    polling = true;
    try {
      const next = await api(endpoint());
      state = next;
      if (connectionFailed) feedback();
      connectionFailed = false;
      $('connection').textContent = '● На связи';
      $('connection').className = 'connection-ok';
      if (forceRender || lastRender !== state.revision) {
        render(); lastRender = state.revision; forceRender = false;
      } else {
        state.players.forEach((player, index) => {
          const node = document.querySelector(`[data-online="${index}"]`);
          if (node) { node.textContent = player.online ? 'На связи' : 'Нет связи — ждём'; node.className = player.online ? 'player-online' : 'player-offline'; }
        });
      }
    } catch (error) {
      connectionFailed = true;
      $('connection').textContent = 'Переподключаемся…';
      $('connection').className = 'connection-lost';
      feedback(error.name === 'AbortError' ? 'Нет ответа от сервера. Восстанавливаем связь…' : error.message);
      if (error.message === 'Подключитесь к комнате заново.') {
        localStorage.removeItem(`durak:${mode}:${code}`); token = null;
        if (mode === 'player') { $('room-panel').hidden = true; $('join-panel').hidden = false; }
      }
    } finally { polling = false; if (token) timer = setTimeout(poll, 1200); }
  }

  $('join-form').addEventListener('submit', async (event) => {
    event.preventDefault(); feedback();
    code = $('room-code').value.trim().toUpperCase();
    const name = $('player-name').value.trim();
    $('join-submit').disabled = true;
    try {
      const result = await api(`${endpoint()}/join`, {name});
      token = result.token;
      localStorage.setItem(`durak:player:${code}`, token);
      history.replaceState(null, '', `/games/join/${code}`);
      await poll(true);
    } catch (error) { feedback(error.name === 'AbortError' ? 'Нет связи с сервером. Попробуйте ещё раз.' : error.message); }
    finally { $('join-submit').disabled = false; }
  });
  $('start-game').addEventListener('click', () => {
    if (state?.phase === 'playing') {
      $('reset-dialog').showModal();
    } else send('start');
  });
  $('reset-cancel').addEventListener('click', () => $('reset-dialog').close());
  $('reset-confirm').addEventListener('click', () => { $('reset-dialog').close(); send('reset'); });
  if (document.fullscreenEnabled && mode === 'host') {
    $('table-fullscreen').hidden = false;
    $('table-fullscreen').addEventListener('click', async () => {
      try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
      catch { feedback('Полный экран можно включить в меню браузера.'); }
    });
  }
  window.addEventListener('online', () => poll(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(true); });
  if (token) { $('room-panel').hidden = false; poll(true); }
  else if (mode === 'player') $('join-panel').hidden = false;
  else feedback('Создайте комнату в игротеке на этом устройстве, чтобы управлять общим экраном.');
})();
