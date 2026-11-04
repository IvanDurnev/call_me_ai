(() => {
  document.querySelectorAll('[data-create-game]').forEach((create) => {
    create.addEventListener('click', async () => {
      create.disabled = true;
      const notice = document.getElementById('games-notice');
      notice.textContent = 'Создаём комнату…';
      const game = create.dataset.createGame;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch('/api/games/rooms', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({game}), signal: controller.signal,
        });
        const room = await response.json();
        if (!response.ok) throw new Error(room.error || 'Не удалось создать комнату.');
        localStorage.setItem(`${game}:host:${room.code}`, room.token);
        window.location.assign(room.url);
      } catch (error) {
        notice.textContent = error.name === 'AbortError' ? 'Нет ответа от сервера. Попробуйте ещё раз.' : error.message;
        create.disabled = false;
      } finally { clearTimeout(timeout); }
    });
  });
  document.querySelectorAll('[data-rules-dialog]').forEach((button) => {
    button.addEventListener('click', () => document.getElementById(button.dataset.rulesDialog).showModal());
  });
  document.querySelectorAll('.games-dialog').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });

  const button = document.getElementById('fullscreen-button');
  const label = document.getElementById('fullscreen-label');
  if (document.fullscreenEnabled) {
    button.hidden = false;
    button.addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
      } catch {
        document.getElementById('games-notice').textContent = 'Не удалось включить полный экран. Попробуйте через меню браузера.';
      }
    });
    document.addEventListener('fullscreenchange', () => {
      label.textContent = document.fullscreenElement ? 'Свернуть экран' : 'На весь экран';
    });
  }
})();
