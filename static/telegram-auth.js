const tgAuthBody = document.body;
const telegramAuthUrl = tgAuthBody.dataset.telegramAuthUrl;
const telegramAuthMode = tgAuthBody.dataset.telegramAuthMode || "background";
const telegramNextUrl = tgAuthBody.dataset.telegramNextUrl || window.location.href;
const telegramAuthStatus = document.getElementById("telegram-auth-status");

const telegramWebApp = window.Telegram?.WebApp;
if (telegramWebApp) {
  telegramWebApp.ready();
  telegramWebApp.expand();
}

async function bootstrapTelegramAuth() {
  if (!telegramAuthUrl || !telegramWebApp?.initData) {
    if (telegramAuthStatus) {
      telegramAuthStatus.textContent = "Не удалось получить данные Telegram mini app.";
    }
    return;
  }

  try {
    const response = await fetch(telegramAuthUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        init_data: telegramWebApp.initData,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      if (payload.close_app && telegramWebApp?.close) {
        telegramWebApp.close();
        return;
      }
      throw new Error(payload.error || "Не удалось подключить аккаунт Telegram.");
    }

    if (telegramAuthMode === "redirect") {
      window.location.replace(telegramNextUrl);
    }
  } catch (error) {
    if (telegramAuthStatus) {
      telegramAuthStatus.textContent = error.message || "Не удалось подключить аккаунт Telegram.";
    }
  }
}

bootstrapTelegramAuth();
