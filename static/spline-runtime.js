import { Application } from 'https://unpkg.com/@splinetool/runtime@latest/build/runtime.js';

const body = document.body;
const videoCallEnabled = body.dataset.videoCallEnabled === 'true';

if (!videoCallEnabled) {
  // No-op on non-video calls.
} else {
  const interiorCanvas = document.getElementById('spline-interior-canvas');
  const characterCanvas = document.getElementById('spline-character-canvas');

  const interiorSceneUrl = interiorCanvas?.dataset.sceneUrl || '';
  const characterSceneUrl = characterCanvas?.dataset.sceneUrl || '';
  const mouthOpenObjectName = body.dataset.videoMouthOpenObjectName || 'MouthOpen';
  const mouthClosedObjectName = body.dataset.videoMouthClosedObjectName || 'MouthClosed';
  const characterSafeZoom = Number(body.dataset.videoCharacterZoom || '0.38') || 0.38;

  let characterApp = null;
  let mouthOpenObject = null;
  let mouthClosedObject = null;

  function looksLikeSplineCode(url) {
    return /\.splinecode(\?|$)/i.test(String(url || ''));
  }

  async function canLoadSceneUrl(url) {
    try {
      const response = await fetch(url, { method: 'GET', mode: 'cors' });
      return response.ok;
    } catch {
      return false;
    }
  }

  function setMouthState(speaking) {
    if (mouthOpenObject) {
      mouthOpenObject.visible = Boolean(speaking);
    }
    if (mouthClosedObject) {
      mouthClosedObject.visible = !Boolean(speaking);
    }
  }

  async function loadInterior() {
    if (!interiorCanvas || !looksLikeSplineCode(interiorSceneUrl)) {
      return;
    }
    try {
      const available = await canLoadSceneUrl(interiorSceneUrl);
      if (!available) {
        console.warn('[Spline] Interior scene URL is not publicly accessible (.splinecode returned non-OK). Use Export > Code URL or self-hosted .splinecode.');
        return;
      }
      const interiorApp = new Application(interiorCanvas);
      await interiorApp.load(interiorSceneUrl);
    } catch (error) {
      console.warn('[Spline] Failed to load interior scene:', error);
    }
  }

  async function loadCharacter() {
    if (!characterCanvas || !looksLikeSplineCode(characterSceneUrl)) {
      return;
    }

    try {
      const available = await canLoadSceneUrl(characterSceneUrl);
      if (!available) {
        console.warn('[Spline] Character scene URL is not publicly accessible (.splinecode returned non-OK). Use Export > Code URL or self-hosted .splinecode.');
        return;
      }
      characterApp = new Application(characterCanvas);
      await characterApp.load(characterSceneUrl);

      // Force a safer camera framing so the whole character fits in view.
      if (typeof characterApp.setZoom === 'function') {
        const safeZoom = Math.max(0.2, Math.min(1, characterSafeZoom));
        characterApp.setZoom(safeZoom);
        // Some scenes apply internal camera actions right after load.
        // Re-apply zoom to guarantee full-character framing.
        window.setTimeout(() => {
          try {
            characterApp?.setZoom?.(safeZoom);
          } catch {
            // Best effort.
          }
        }, 150);
        window.setTimeout(() => {
          try {
            characterApp?.setZoom?.(safeZoom);
          } catch {
            // Best effort.
          }
        }, 600);
      }

      mouthOpenObject = characterApp.findObjectByName(mouthOpenObjectName) || null;
      mouthClosedObject = characterApp.findObjectByName(mouthClosedObjectName) || null;

      // Initial state: character is silent.
      setMouthState(false);
    } catch (error) {
      console.warn('[Spline] Failed to load character scene:', error);
    }
  }

  window.addEventListener('call:speaking-state', (event) => {
    const speaking = Boolean(event?.detail?.speaking);
    setMouthState(speaking);
  });

  void loadInterior();
  void loadCharacter();
}
