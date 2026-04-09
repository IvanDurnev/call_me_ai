const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

function formatPayload(payload) {
  return JSON.stringify(payload, null, 2);
}

async function callVoiceAction(endpoint, name) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

  const payload = await response.json().catch(() => ({ ok: false, error: "Invalid server response." }));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

function bindVoiceActions() {
  const cards = document.querySelectorAll(".voice-card[data-voice-name]");
  cards.forEach((card, index) => {
    const voiceName = card.dataset.voiceName;
    const convertButton = card.querySelector(".js-convert-wav");
    const consentButton = card.querySelector(".js-create-consent");
    const voiceButton = card.querySelector(".js-create-voice");
    const result = card.querySelector(".voice-result") || document.getElementById(`voice-result-${index}`);

    async function runAction(button, endpoint, pendingLabel) {
      if (!button || !result) return;
      const previousLabel = button.textContent;
      button.disabled = true;
      result.textContent = pendingLabel;

      try {
        const payload = await callVoiceAction(endpoint, voiceName);
        result.textContent = formatPayload(payload);
      } catch (error) {
        result.textContent = error instanceof Error ? error.message : String(error);
      } finally {
        button.disabled = false;
        button.textContent = previousLabel;
      }
    }

    convertButton?.addEventListener("click", () => {
      runAction(convertButton, "/api/voices/convert-wav", "Converting sample to WAV...");
    });

    consentButton?.addEventListener("click", () => {
      runAction(consentButton, "/api/voices/create-consent", "Creating consent...");
    });

    voiceButton?.addEventListener("click", () => {
      runAction(voiceButton, "/api/voices/create-voice", "Creating voice...");
    });
  });
}

bindVoiceActions();
