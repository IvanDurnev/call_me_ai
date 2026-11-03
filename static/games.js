(() => {
  const create = document.getElementById('create-room');
  create.addEventListener('click', async () => {
    create.disabled = true;
    const notice = document.getElementById('games-notice');
    notice.textContent = 'Создаём комнату…';
    try {
      const response = await fetch('/api/games/rooms', {method: 'POST'});
      const room = await response.json();
      if (!response.ok) throw new Error(room.error || 'Не удалось создать комнату.');
      localStorage.setItem(`durak:host:${room.code}`, room.token);
      window.location.assign(room.url);
    } catch (error) {
      notice.textContent = error.message || 'Нет связи с сервером. Попробуйте ещё раз.';
      create.disabled = false;
    }
  });
  const dialog = document.getElementById('rules-dialog');
  document.getElementById('rules-button').addEventListener('click', () => dialog.showModal());
  dialog.addEventListener('click', (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
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
