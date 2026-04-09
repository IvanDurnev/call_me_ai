const maxAuthBody = document.body;
const maxAuthUrl = maxAuthBody.dataset.maxAuthUrl;
const maxAuthMode = maxAuthBody.dataset.maxAuthMode || "background";
const maxNextUrl = maxAuthBody.dataset.maxNextUrl || window.location.href;
const maxAuthStatus = document.getElementById("max-auth-status");

async function bootstrapMaxAuth() {
  if (!maxAuthUrl || !window.WebApp?.initData) {
    if (maxAuthStatus) {
      maxAuthStatus.textContent = "Не удалось получить данные MAX mini app.";
    }
    return;
  }

  try {
    const response = await fetch(maxAuthUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        init_data: window.WebApp.initData,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      if (payload.close_app && window.WebApp?.close) {
        window.WebApp.close();
        return;
      }
      throw new Error(payload.error || "Не удалось подключить аккаунт MAX.");
    }

    if (maxAuthStatus && payload.user?.name) {
      maxAuthStatus.textContent = `Привет, ${payload.user.name}! Открываем приложение…`;
    }

    if (maxAuthMode === "redirect") {
      window.location.replace(maxNextUrl);
    }
  } catch (error) {
    if (window.WebApp?.close && error?.message?.includes("не найдена привязанная учётная запись")) {
      window.WebApp.close();
      return;
    }
    if (maxAuthStatus) {
      maxAuthStatus.textContent = error.message || "Не удалось подключить аккаунт MAX.";
    }
  }
}

bootstrapMaxAuth();
