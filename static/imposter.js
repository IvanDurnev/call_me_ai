(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const mode = document.body.dataset.mode;
  const code = document.body.dataset.code;
  const storageKey = `imposter:${mode}:${code}`;
  let token;
  try { token = localStorage.getItem(storageKey); } catch { /* A join will explain unavailable storage. */ }
  let state = null;
  let busy = false;
  let polling = false;
  let timer;
  let rendered = -1;
  let forceRender = false;
  let revealed = false;
  let currentMatch = -1;
  let reconnecting = false;
  let serverOffset = 0;
  const drafts = new Map();
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
    busy = true; feedback();
    // Keep text fields mounted and their contents intact while a request is in flight.
    document.querySelectorAll('#room button, #room input').forEach((node) => { node.disabled = true; });
    try {
      await api(`${base}/action`, {action, revision: state.revision, phase: state.phase,
        match: state.match, ballot: state.ballot, ...extra});
    } catch (error) {
      feedback(error.name === 'AbortError' ? 'Нет ответа от сервера. Проверяем, принято ли действие…' : error.message);
    } finally {
      busy = false;
      await poll(true);
    }
  }

  function textForm(action, label, maxLength, placeholder) {
    const form = element('form', undefined, 'imposter-text-form');
    const draftKey = `${state.match}:${state.phase}:${state.round}:${state.turn}`;
    const inputLabel = element('label', action === 'clue' ? 'Ваша ассоциация' : 'Какое слово загадано?');
    const input = element('input'); input.name = 'text'; input.required = true;
    input.value = drafts.get(draftKey) || '';
    input.addEventListener('input', () => drafts.set(draftKey, input.value));
    input.maxLength = maxLength; input.placeholder = placeholder; input.autocomplete = 'off';
    input.disabled = busy; inputLabel.append(input);
    const submit = element('button', label, 'durak-primary'); submit.type = 'submit'; submit.disabled = busy;
    form.append(inputLabel, submit);
    form.addEventListener('submit', (event) => { event.preventDefault(); if (input.value.trim()) send(action, {text: input.value.trim()}); });
    return form;
  }

  function renderSecret() {
    if (!state) return;
    $('reveal-secret').disabled = busy;
    $('hide-secret').disabled = busy;
    const show = mode === 'player' && !['lobby', 'finished'].includes(state.phase);
    $('secret-panel').hidden = !show;
    $('secret-content').hidden = !revealed;
    $('reveal-secret').hidden = revealed;
    $('hide-secret').hidden = !revealed;
    // The DOM contains the secret only while the player explicitly opens it.
    $('secret-role').textContent = show && revealed ? (state.role === 'imposter' ? 'Вы — импостер' : 'Вы знаете слово') : '';
    $('secret-word').textContent = show && revealed ? (state.role === 'imposter' ? '?' : state.word) : '';
    $('secret-help').textContent = show && revealed ? (state.role === 'imposter' ? 'Слушайте ассоциации и не выдавайте себя.' : 'Дайте подсказку своим, но не раскройте слово импостеру.') : '';
  }

  function render() {
    if (!state) return;
    const {phase, you} = state;
    const host = mode === 'host';
    const lobby = phase === 'lobby';
    if (state.match !== currentMatch) { revealed = false; drafts.clear(); currentMatch = state.match; }
    $('join-panel').hidden = true; $('room').hidden = false;
    $('room-code').textContent = `Комната ${code} · ${state.players.length}/12 игроков`;
    const titles = {
      lobby: ['Слово знают почти все', 'Собираемся играть', 'Подключите от 3 до 12 игроков с телефонов.'],
      reveal: ['Секретные роли', 'Посмотрите свою роль', `Готовы ${state.ready_count} из ${state.players.length}. Слово не произносим вслух.`],
      clues: [`Круг ассоциаций ${state.round}/3`, `Сейчас ${playerName(state.order[state.turn])}`, 'Назовите ассоциацию вслух и отправьте её с телефона.'],
      discussion: ['Время обсудить', 'Кто из вас блефует?', 'Обсудите ассоциации. Начните голосование или сыграйте ещё один круг.'],
      voting: [state.ballot === 2 ? 'Повторное голосование' : 'Тайное голосование', 'Кого вы подозреваете?', `Получено ${state.vote_count} из ${state.players.length} голосов.${state.ballot === 2 ? ' Выбираем среди лидеров. При новой ничьей победит импостер.' : ' За себя голосовать нельзя.'}`],
      guess: ['Импостер раскрыт', `${playerName(state.imposter)}, угадайте слово`, 'Последний шанс: одна попытка и 30 секунд. Не подсказывайте!'],
      finished: ['Раунд завершён', state.winner === 'team' ? 'Победила компания!' : 'Победил импостер!', ''],
    };
    const title = titles[phase];
    $('phase-label').textContent = title[0]; $('stage-title').textContent = title[1]; $('stage-message').textContent = title[2];
    $('lobby-intro').hidden = !lobby;
    renderSecret();
    $('personal-actions').replaceChildren();
    if (!host) {
      const actions = $('personal-actions');
      if (phase === 'reveal') {
        if (state.players[you].ready) actions.append(element('p', 'Вы готовы. Ждём остальных.', 'imposter-waiting'));
        else {
          const ready = actionButton('Я готов', 'ready', {}, true);
          ready.disabled = busy || !revealed;
          actions.append(ready);
        }
      } else if (phase === 'clues' && state.order[state.turn] === you) {
        actions.append(textForm('clue', 'Отправить ассоциацию', 32, 'Одно слово или короткая фраза'));
      } else if (phase === 'voting') {
        if (state.own_vote !== null) actions.append(element('p', `Ваш голос за ${playerName(state.own_vote)} принят.`, 'imposter-waiting'));
        else {
          const votes = element('div', undefined, 'imposter-vote-options');
          for (const candidate of state.candidates) {
            if (candidate !== you) votes.append(actionButton(playerName(candidate), 'vote', {target: candidate}));
          }
          actions.append(votes);
        }
      } else if (phase === 'guess' && state.imposter === you) {
        actions.append(textForm('guess', 'Это моё окончательное слово', 64, 'Ваша единственная попытка'));
      }
    }
    $('clue-section').hidden = lobby || phase === 'reveal' || !state.clues.length;
    $('clues').replaceChildren();
    for (const clue of state.clues) {
      const item = element('li');
      item.append(element('small', `${playerName(clue.player)} · круг ${clue.round}`), element('strong', clue.text));
      $('clues').append(item);
    }
    $('players').replaceChildren();
    state.players.forEach((player, index) => {
      const item = element('div', undefined, `durak-player${index === you ? ' is-you' : ''}${phase === 'clues' && state.order[state.turn] === index ? ' is-speaking' : ''}`);
      const text = element('div'); text.append(element('strong', player.name));
      const detail = phase === 'reveal' ? (player.ready ? 'Готов ✓' : 'Смотрит роль') : phase === 'voting' ? (player.voted ? 'Проголосовал ✓' : 'Выбирает') : phase === 'clues' && state.order[state.turn] === index ? 'Сейчас говорит' : 'В игре';
      text.append(element('small', detail));
      const online = element('small', player.online ? 'На связи' : 'Нет связи — ждём', player.online ? 'player-online' : 'player-offline');
      online.dataset.online = String(index); text.append(online);
      item.append(element('span', player.name.slice(0, 1).toUpperCase(), 'player-avatar'), text);
      if (host && lobby) {
        const remove = actionButton('×', 'remove', {target: index}); remove.className = 'remove-player';
        remove.setAttribute('aria-label', `Убрать игрока ${player.name}`); item.append(remove);
      }
      $('players').append(item);
    });
    $('invite').hidden = !host || !lobby;
    if (host && lobby) {
      if (!$('qr').getAttribute('src')) $('qr').src = `${base}/qr.svg`;
      $('invite-code').textContent = code; $('invite-link').textContent = state.join_url.replace(/^https?:\/\//, ''); $('invite-link').href = state.join_url;
    }
    $('host-actions').replaceChildren();
    if (host) {
      const actions = $('host-actions');
      if (lobby || phase === 'finished') {
        const start = actionButton(phase === 'finished' ? 'Следующий раунд →' : 'Начать игру →', 'start', {}, true);
        start.disabled = busy || state.players.length < 3; actions.append(start);
      }
      if (phase === 'discussion') {
        actions.append(actionButton('Начать голосование', 'voting', {}, true));
        if (state.round < 3) actions.append(actionButton('Ещё круг ассоциаций', 'another_round'));
      }
      if (phase === 'clues') actions.append(actionButton('Пропустить этот ход', 'skip'));
      if (!lobby) {
        const reset = element('button', 'Вернуться в лобби', 'durak-secondary'); reset.type = 'button'; reset.disabled = busy;
        reset.addEventListener('click', () => $('confirm-dialog').showModal()); actions.append(reset);
      }
    }
    $('outcome').hidden = phase !== 'finished';
    $('outcome-title').textContent = phase === 'finished' ? `Импостер — ${playerName(state.imposter)}` : '';
    $('outcome-reason').textContent = state.reason || '';
    $('outcome-details').replaceChildren();
    if (phase === 'finished') {
      $('outcome-details').append(element('p', `Секретное слово: ${state.word}`, 'imposter-answer'));
      if (state.guess) $('outcome-details').append(element('p', `Попытка импостера: ${state.guess}`));
      if (Object.keys(state.tally || {}).length) {
        const tally = element('ul', undefined, 'imposter-tally');
        for (const [index, count] of Object.entries(state.tally)) tally.append(element('li', `${playerName(Number(index))}: ${count}`));
        $('outcome-details').append(tally);
      }
    }
    $('guess-timer').hidden = phase !== 'guess'; updateClock();
  }

  function updateClock() {
    if (state?.phase === 'guess') {
      const seconds = Math.max(0, Math.ceil(state.deadline - (Date.now() / 1000 + serverOffset)));
      $('guess-timer').textContent = `Осталось ${seconds} сек.`;
    }
  }
  setInterval(updateClock, 250);

  async function poll(force = false) {
    clearTimeout(timer); if (force) forceRender = true;
    if (!token || polling) return;
    polling = true;
    try {
      const next = await api(base);
      if (next.game !== 'imposter') throw new Error('Эта комната относится к другой игре. Откройте её через игротеку.');
      state = next; serverOffset = state.server_time - Date.now() / 1000;
      if (reconnecting) feedback(); reconnecting = false;
      $('connection').textContent = '● На связи'; $('connection').className = 'connection-ok';
      if (forceRender || rendered !== state.revision) { render(); rendered = state.revision; forceRender = false; }
      else state.players.forEach((player, index) => {
        const node = document.querySelector(`[data-online="${index}"]`);
        if (node) { node.textContent = player.online ? 'На связи' : 'Нет связи — ждём'; node.className = player.online ? 'player-online' : 'player-offline'; }
      });
    } catch (error) {
      reconnecting = true; $('connection').textContent = 'Переподключаемся…'; $('connection').className = 'connection-lost';
      feedback(error.name === 'AbortError' ? 'Нет ответа от сервера. Восстанавливаем связь…' : error.message);
      if (error.message === 'Подключитесь к комнате заново.') {
        token = null; localStorage.removeItem(storageKey); $('room').hidden = true;
        if (mode === 'player') $('join-panel').hidden = false;
      }
    } finally { polling = false; if (token) timer = setTimeout(poll, 1000); }
  }

  $('join-form').addEventListener('submit', async (event) => {
    event.preventDefault(); feedback(); $('join-submit').disabled = true;
    try {
      const result = await api(`${base}/join`, {name: $('player-name').value.trim()});
      localStorage.setItem(storageKey, result.token); token = result.token; await poll(true);
    } catch (error) { feedback(error.name === 'AbortError' ? 'Нет связи с сервером.' : error.message); }
    finally { $('join-submit').disabled = false; }
  });
  $('reveal-secret').addEventListener('click', () => { revealed = true; renderSecret(); render(); });
  $('hide-secret').addEventListener('click', () => { revealed = false; renderSecret(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { revealed = false; renderSecret(); }
    else poll(true);
  });
  window.addEventListener('online', () => poll(true));
  $('cancel-reset').addEventListener('click', () => $('confirm-dialog').close());
  $('confirm-reset').addEventListener('click', () => { $('confirm-dialog').close(); send('reset'); });
  if (document.fullscreenEnabled && mode === 'host') {
    $('fullscreen').hidden = false;
    $('fullscreen').addEventListener('click', async () => {
      try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
      catch { feedback('Включите полный экран через меню браузера.'); }
    });
  }
  if (token) { $('room').hidden = false; poll(true); }
  else if (mode === 'player') $('join-panel').hidden = false;
  else feedback('Создайте комнату в игротеке на этом устройстве.');
})();
