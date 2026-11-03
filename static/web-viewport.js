(() => {
  const root = document.documentElement;
  const stage = document.querySelector(".web-chat-stage");
  const composer = document.getElementById("web-chat-input");
  const viewport = window.visualViewport;
  let frame = null;

  function updateViewport() {
    frame = null;
    // Do not counteract the user's pinch zoom or pan a zoomed document.
    if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
    const height = viewport?.height || window.innerHeight;
    const top = Math.max(0, viewport?.offsetTop || 0);
    const atBottom = stage && stage.scrollHeight - stage.scrollTop - stage.clientHeight < 48;
    root.style.setProperty("--web-viewport-height", `${Math.round(height)}px`);
    root.style.setProperty("--web-viewport-top", `${Math.round(top)}px`);
    // Only the message area scrolls; never scroll the document to reveal an input.
    if (stage && (atBottom || document.activeElement === composer)) {
      stage.scrollTop = stage.scrollHeight;
    }
  }

  function scheduleUpdate() {
    if (frame === null) frame = requestAnimationFrame(updateViewport);
  }

  viewport?.addEventListener("resize", scheduleUpdate);
  viewport?.addEventListener("scroll", scheduleUpdate);
  window.addEventListener("resize", scheduleUpdate);
  window.addEventListener("pageshow", scheduleUpdate);
  window.addEventListener("orientationchange", scheduleUpdate);
  document.addEventListener("focusin", scheduleUpdate);
  document.addEventListener("focusout", () => {
    scheduleUpdate();
    // Safari may restore its viewport after the keyboard closing animation.
    window.setTimeout(scheduleUpdate, 300);
  });
  updateViewport();
})();
