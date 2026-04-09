const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const stateNode = document.getElementById("heroes-initial-state");
const initialState = JSON.parse(stateNode?.textContent || "{}");

let heroes = initialState.heroes || [];
let selectedSlug = heroes[0]?.slug || null;
let pricingPlans = initialState.pricing_plans || [];
let selectedPlanCode = pricingPlans[0]?.code || null;
let previewAudioUrl = null;
const PREVIEW_VERSION = "20260409c";

const heroesList = document.getElementById("heroes-list");
const heroEditor = document.getElementById("hero-editor");
const createForm = document.getElementById("hero-create-form");
const createStatus = document.getElementById("hero-create-status");
const plansList = document.getElementById("plans-list");
const planEditor = document.getElementById("plan-editor");
const planCreateForm = document.getElementById("plan-create-form");
const planCreateStatus = document.getElementById("plan-create-status");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectedHero() {
  return heroes.find((hero) => hero.slug === selectedSlug) || heroes[0] || null;
}

function selectedPlan() {
  return pricingPlans.find((plan) => plan.code === selectedPlanCode) || pricingPlans[0] || null;
}

function setStatus(node, message, isError = false) {
  if (!node) {
    return;
  }
  node.textContent = message;
  node.classList.toggle("is-error", isError);
}

function renderHeroList() {
  if (!heroesList) {
    return;
  }
  heroesList.innerHTML = heroes.map((hero) => `
    <button class="hero-tab${hero.slug === selectedSlug ? " is-active" : ""}" type="button" data-hero-slug="${escapeHtml(hero.slug)}">
      <span class="hero-tab-avatar">
        ${hero.avatar_url ? `<img src="${escapeHtml(hero.avatar_url)}" alt="${escapeHtml(hero.name)}">` : `<span>${escapeHtml(hero.emoji || "AI")}</span>`}
      </span>
      <span class="hero-tab-copy">
        <strong>${escapeHtml(hero.name)}</strong>
        <span>${escapeHtml(hero.description || "Описание пока пустое.")}</span>
      </span>
    </button>
  `).join("");

  heroesList.querySelectorAll("[data-hero-slug]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedSlug = button.dataset.heroSlug;
      render();
    });
  });
}

function planKindLabel(kind) {
  const option = (initialState.pricing_plan_kind_options || []).find((item) => item.value === kind);
  return option?.label || kind;
}

function renderPlanList() {
  if (!plansList) {
    return;
  }
  plansList.innerHTML = pricingPlans.map((plan) => `
    <button class="hero-tab${plan.code === selectedPlanCode ? " is-active" : ""}" type="button" data-plan-code="${escapeHtml(plan.code)}">
      <span class="hero-tab-avatar tariff-tab-avatar">
        <span>${plan.kind === "unlimited" ? "∞" : "☎"}</span>
      </span>
      <span class="hero-tab-copy">
        <strong>${escapeHtml(plan.name)}</strong>
        <span>${escapeHtml(planKindLabel(plan.kind))} · ${escapeHtml(Number(plan.price || 0).toFixed(2))} ${escapeHtml(plan.currency || "RUB")} · порядок ${escapeHtml(plan.sort_order ?? 0)}</span>
      </span>
    </button>
  `).join("");

  plansList.querySelectorAll("[data-plan-code]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedPlanCode = button.dataset.planCode;
      render();
    });
  });
}

function renderVoiceOptions(hero) {
  return (initialState.voice_options || []).map((option) => `
    <option value="${escapeHtml(option.value)}" ${option.value === hero.voice ? "selected" : ""}>
      ${escapeHtml(option.label)}${option.recommended ? " recommended" : ""}
    </option>
  `).join("");
}

function renderModelOptions(hero) {
  const currentValue = hero.realtime_model || "";
  const options = (initialState.realtime_model_options || []).map((option) => `
    <option value="${escapeHtml(option)}" ${option === currentValue ? "selected" : ""}>${escapeHtml(option)}</option>
  `).join("");
  return `<option value="">По умолчанию проекта</option>${options}`;
}

function renderTranscriptionOptions(hero) {
  const currentValue = hero.input_transcription_model || "";
  const options = (initialState.transcription_model_options || []).map((option) => `
    <option value="${escapeHtml(option)}" ${option === currentValue ? "selected" : ""}>${escapeHtml(option)}</option>
  `).join("");
  return `<option value="">Выключить транскрипцию</option>${options}`;
}

function renderNoiseReductionOptions(hero) {
  const currentValue = hero.noise_reduction_type || "none";
  return (initialState.noise_reduction_options || []).map((option) => `
    <option value="${escapeHtml(option)}" ${option === currentValue ? "selected" : ""}>${escapeHtml(option)}</option>
  `).join("");
}

function previewUrlForVoice(voice) {
  return `/static/voices/previews/${encodeURIComponent(String(voice || "").trim())}.mp3?v=${PREVIEW_VERSION}`;
}

function renderEditor() {
  if (!heroEditor) {
    return;
  }
  const hero = selectedHero();
  if (!hero) {
    heroEditor.innerHTML = '<div class="hero-editor-empty">Герои не найдены.</div>';
    return;
  }

  heroEditor.innerHTML = `
    <form class="hero-form" id="hero-form">
      <div class="hero-preview">
        <div class="avatar-orb hero-preview-avatar${hero.avatar_url ? " avatar-orb-image" : ""}">
          ${hero.avatar_url ? `<img src="${escapeHtml(hero.avatar_url)}" alt="${escapeHtml(hero.name)}">` : `<span>${escapeHtml(hero.emoji || "AI")}</span>`}
        </div>
        <div class="hero-preview-copy">
          <h2>${escapeHtml(hero.name)}</h2>
          <p>${escapeHtml(hero.description || "Заполните описание, чтобы герой звучал и выглядел осмысленно.")}</p>
          <span class="hero-meta">Slug: ${escapeHtml(hero.slug)}</span>
        </div>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>Основное</h3>
          <p>Это влияет на карточку героя и его общий образ в звонке.</p>
        </div>
        <div class="hero-field-grid">
          <label class="hero-field">
            <span>Имя</span>
            <input name="name" type="text" value="${escapeHtml(hero.name)}" maxlength="255" required>
          </label>
          <label class="hero-field">
            <span>Эмодзи</span>
            <input name="emoji" type="text" value="${escapeHtml(hero.emoji || "")}" maxlength="16" placeholder="✨">
          </label>
        </div>
        <label class="hero-field">
          <span>Описание</span>
          <textarea name="description" rows="4" placeholder="Кто это, как он разговаривает, в чём его характер.">${escapeHtml(hero.description || "")}</textarea>
        </label>
        <label class="hero-field">
          <span>Голос OpenAI</span>
          <select name="voice" id="hero-voice-select">${renderVoiceOptions(hero)}</select>
        </label>
        <div class="hero-voice-preview">
          <button class="call-btn call-btn-secondary" type="button" id="voice-preview-btn">Прослушать голос</button>
          <audio id="voice-preview-audio" controls preload="metadata" src="${escapeHtml(previewUrlForVoice(hero.voice))}"></audio>
        </div>
        <label class="hero-field hero-field-inline">
          <input name="is_active" type="checkbox" ${hero.is_active ? "checked" : ""}>
          <span>Герой активен и показывается в /start</span>
        </label>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>Файлы героя</h3>
          <p>Аватар показывается во время звонка, а текстовая база знаний подмешивается в системные инструкции.</p>
        </div>
        <div class="hero-upload-grid">
          <div class="hero-upload-card">
            <div class="hero-upload-copy">
              <strong>База знаний</strong>
              <span>${escapeHtml(hero.knowledge_file_name || "Файл ещё не загружен")}</span>
              <p>${escapeHtml(hero.knowledge_summary || "")}</p>
            </div>
            <label class="call-btn call-btn-secondary hero-upload-btn">
              <input id="knowledge-upload" type="file" accept=".txt,.md,.markdown,.json,.csv,.yaml,.yml" hidden>
              Загрузить текстовый файл
            </label>
          </div>
          <div class="hero-upload-card">
            <div class="hero-upload-copy">
              <strong>Аватар</strong>
              <span>${hero.avatar_url ? "Аватар загружен" : "Аватар ещё не загружен"}</span>
              <p>Поддерживаются jpg, png, webp и gif.</p>
            </div>
            <label class="call-btn call-btn-secondary hero-upload-btn">
              <input id="avatar-upload" type="file" accept=".jpg,.jpeg,.png,.webp,.gif" hidden>
              Загрузить картинку
            </label>
          </div>
        </div>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>Realtime API</h3>
          <p>Отдельный блок для точной настройки модели, инструкций, распознавания и аудиовыхода.</p>
        </div>
        <div class="hero-field-grid">
          <label class="hero-field">
            <span>Realtime model</span>
            <select name="realtime_model">${renderModelOptions(hero)}</select>
          </label>
          <label class="hero-field">
            <span>Input transcription model</span>
            <select name="input_transcription_model">${renderTranscriptionOptions(hero)}</select>
          </label>
          <label class="hero-field">
            <span>Input language</span>
            <input name="input_transcription_language" type="text" value="${escapeHtml(hero.input_transcription_language || "")}" placeholder="ru">
          </label>
          <label class="hero-field">
            <span>Noise reduction</span>
            <select name="noise_reduction_type">${renderNoiseReductionOptions(hero)}</select>
          </label>
          <label class="hero-field">
            <span>Max output tokens</span>
            <input name="max_output_tokens" type="text" value="${escapeHtml(hero.max_output_tokens ?? "inf")}" placeholder="inf">
          </label>
          <label class="hero-field">
            <span>Output audio speed</span>
            <input name="output_audio_speed" type="number" min="0.25" max="1.5" step="0.01" value="${escapeHtml(hero.output_audio_speed ?? 1)}">
          </label>
        </div>
        <label class="hero-field">
          <span>Greeting prompt</span>
          <textarea name="greeting_prompt" rows="3" placeholder="Как герой начинает разговор первым.">${escapeHtml(hero.greeting_prompt || "")}</textarea>
        </label>
        <label class="hero-field">
          <span>System prompt</span>
          <textarea name="system_prompt" rows="5" placeholder="Базовые инструкции для героя.">${escapeHtml(hero.system_prompt || "")}</textarea>
        </label>
        <label class="hero-field">
          <span>Instructions override</span>
          <textarea name="instructions_override" rows="4" placeholder="Если заполнить, будет использовано как override поверх session instructions.">${escapeHtml(hero.instructions_override || "")}</textarea>
        </label>
        <label class="hero-field">
          <span>Transcription prompt</span>
          <textarea name="input_transcription_prompt" rows="3" placeholder="Подсказка для распознавания речи.">${escapeHtml(hero.input_transcription_prompt || "")}</textarea>
        </label>
      </div>

      <div class="hero-actions">
        <button class="call-btn call-btn-primary" type="submit">Сохранить героя</button>
        <button class="call-btn call-btn-danger" type="button" id="hero-delete-btn">Удалить героя</button>
        <span class="hero-status" id="hero-status">Изменения готовы к сохранению.</span>
      </div>
    </form>
  `;

  const form = document.getElementById("hero-form");
  const statusNode = document.getElementById("hero-status");
  const knowledgeInput = document.getElementById("knowledge-upload");
  const avatarInput = document.getElementById("avatar-upload");
  const deleteButton = document.getElementById("hero-delete-btn");
  const previewButton = document.getElementById("voice-preview-btn");
  const previewAudio = document.getElementById("voice-preview-audio");
  const voiceSelect = document.getElementById("hero-voice-select");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setStatus(statusNode, "Сохраняю настройки героя...");
    const formData = new FormData(form);
    const payload = {
      name: formData.get("name"),
      emoji: formData.get("emoji"),
      description: formData.get("description"),
      voice: formData.get("voice"),
      is_active: formData.get("is_active") === "on",
      realtime_model: formData.get("realtime_model"),
      input_transcription_model: formData.get("input_transcription_model"),
      input_transcription_language: formData.get("input_transcription_language"),
      input_transcription_prompt: formData.get("input_transcription_prompt"),
      noise_reduction_type: formData.get("noise_reduction_type"),
      max_output_tokens: formData.get("max_output_tokens"),
      output_audio_format: "pcm16",
      output_audio_speed: formData.get("output_audio_speed"),
      greeting_prompt: formData.get("greeting_prompt"),
      system_prompt: formData.get("system_prompt"),
      instructions_override: formData.get("instructions_override"),
    };

    try {
      const response = await fetch(`/api/heroes/${hero.slug}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось сохранить героя.");
      }

      updateHero(result.hero);
      setStatus(statusNode, "Настройки героя сохранены.");
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось сохранить героя.", true);
    }
  });

  deleteButton.addEventListener("click", async () => {
    const confirmed = window.confirm(`Удалить персонажа «${hero.name}»? Это действие нельзя отменить.`);
    if (!confirmed) {
      return;
    }

    setStatus(statusNode, "Удаляю героя...");
    try {
      const response = await fetch(`/api/heroes/${hero.slug}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось удалить героя.");
      }

      heroes = heroes.filter((entry) => entry.slug !== hero.slug);
      selectedSlug = heroes[0]?.slug || null;
      render();
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось удалить героя.", true);
    }
  });

  previewButton.addEventListener("click", async () => {
    const selectedVoice = voiceSelect.value;
    if (!selectedVoice) {
      setStatus(statusNode, "Сначала выберите голос.", true);
      return;
    }

    setStatus(statusNode, "Запускаю предпрослушивание голоса...");
    previewButton.disabled = true;

    try {
      try {
        await previewAudio.play();
        setStatus(statusNode, "Предпрослушивание играет.");
      } catch (_error) {
        setStatus(statusNode, "Предпрослушивание готово. Нажмите Play на плеере.", false);
      }
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось получить предпрослушивание.", true);
    } finally {
      previewButton.disabled = false;
    }
  });

  voiceSelect.addEventListener("change", () => {
    const selectedVoice = voiceSelect.value;
    previewAudio.pause();
    previewAudio.currentTime = 0;
    previewAudio.src = previewUrlForVoice(selectedVoice);
    previewAudio.load();
    setStatus(statusNode, "Файл предпрослушивания обновлён. Можно нажать Play.", false);
  });

  knowledgeInput.addEventListener("change", async () => {
    if (!knowledgeInput.files?.length) {
      return;
    }
    await uploadFile(hero.slug, knowledgeInput.files[0], "knowledge", statusNode);
    knowledgeInput.value = "";
  });

  avatarInput.addEventListener("change", async () => {
    if (!avatarInput.files?.length) {
      return;
    }
    await uploadFile(hero.slug, avatarInput.files[0], "avatar", statusNode);
    avatarInput.value = "";
  });
}

function renderPlanKindOptions(currentValue) {
  return (initialState.pricing_plan_kind_options || []).map((option) => `
    <option value="${escapeHtml(option.value)}" ${option.value === currentValue ? "selected" : ""}>${escapeHtml(option.label)}</option>
  `).join("");
}

function syncPlanConditionalFields(form) {
  const kind = form.elements.kind.value;
  const callsField = form.querySelector('[data-plan-field="minutes_included"]');
  const periodField = form.querySelector('[data-plan-field="period_days"]');
  const callsInput = form.elements.minutes_included;
  const periodInput = form.elements.period_days;

  const isCallPackage = kind === "call_package";
  callsField.hidden = !isCallPackage;
  periodField.hidden = isCallPackage;
  callsInput.required = isCallPackage;
  periodInput.required = !isCallPackage;
}

function renderPlanEditor() {
  if (!planEditor) {
    return;
  }
  const plan = selectedPlan();
  if (!plan) {
    planEditor.innerHTML = '<div class="hero-editor-empty">Тарифы пока не созданы.</div>';
    return;
  }

  planEditor.innerHTML = `
    <form class="hero-form" id="plan-form">
      <div class="hero-preview">
        <div class="avatar-orb hero-preview-avatar tariff-preview-avatar">
          <span>${plan.kind === "unlimited" ? "∞" : "☎"}</span>
        </div>
        <div class="hero-preview-copy">
          <h2>${escapeHtml(plan.name)}</h2>
          <p>${escapeHtml(plan.description || "Заполните описание, чтобы тариф было проще выбрать в кабинете.")}</p>
          <span class="hero-meta">Code: ${escapeHtml(plan.code)}</span>
        </div>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>Параметры тарифа</h3>
          <p>Выберите тип тарифа и его условия. Для пакета минут задаётся количество минут, для безлимита период в днях.</p>
        </div>
        <div class="hero-field-grid">
          <label class="hero-field">
            <span>Название</span>
            <input name="name" type="text" value="${escapeHtml(plan.name)}" maxlength="255" required>
          </label>
          <label class="hero-field">
            <span>Тип тарифа</span>
            <select name="kind">${renderPlanKindOptions(plan.kind)}</select>
          </label>
          <label class="hero-field">
            <span>Цена</span>
            <input name="price" type="number" min="1" step="0.01" value="${escapeHtml(Number(plan.price || 0).toFixed(2))}" required>
          </label>
          <label class="hero-field">
            <span>Валюта</span>
            <input name="currency" type="text" value="${escapeHtml(plan.currency || "RUB")}" maxlength="8" placeholder="RUB" required>
          </label>
          <label class="hero-field">
            <span>Порядок</span>
            <input name="sort_order" type="number" min="0" step="1" value="${escapeHtml(plan.sort_order ?? 0)}" required>
          </label>
          <label class="hero-field" data-plan-field="minutes_included" ${plan.kind === "call_package" ? "" : "hidden"}>
            <span>Количество минут</span>
            <input name="minutes_included" type="number" min="1" step="1" value="${escapeHtml(plan.minutes_included ?? "")}">
          </label>
          <label class="hero-field" data-plan-field="period_days" ${plan.kind === "unlimited" ? "" : "hidden"}>
            <span>Период в днях</span>
            <input name="period_days" type="number" min="1" step="1" value="${escapeHtml(plan.period_days ?? "")}">
          </label>
        </div>
        <label class="hero-field">
          <span>Описание</span>
          <textarea name="description" rows="4" placeholder="Что получает пользователь с этим тарифом.">${escapeHtml(plan.description || "")}</textarea>
        </label>
        <label class="hero-field hero-field-inline">
          <input name="is_active" type="checkbox" ${plan.is_active ? "checked" : ""}>
          <span>Тариф активен и может показываться в личном кабинете</span>
        </label>
      </div>

      <div class="hero-actions">
        <button class="call-btn call-btn-primary" type="submit">Сохранить тариф</button>
        <button class="call-btn call-btn-danger" type="button" id="plan-delete-btn">Удалить тариф</button>
        <span class="hero-status" id="plan-status">Изменения готовы к сохранению.</span>
      </div>
    </form>
  `;

  const form = document.getElementById("plan-form");
  const statusNode = document.getElementById("plan-status");
  const deleteButton = document.getElementById("plan-delete-btn");
  syncPlanConditionalFields(form);

  form.elements.kind.addEventListener("change", () => {
    syncPlanConditionalFields(form);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setStatus(statusNode, "Сохраняю тариф...");
    const formData = new FormData(form);
    const payload = {
      name: formData.get("name"),
      description: formData.get("description"),
      kind: formData.get("kind"),
      price: formData.get("price"),
      currency: formData.get("currency"),
      sort_order: formData.get("sort_order"),
      minutes_included: formData.get("minutes_included"),
      period_days: formData.get("period_days"),
      is_active: formData.get("is_active") === "on",
    };

    try {
      const response = await fetch(`/api/pricing-plans/${plan.code}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось сохранить тариф.");
      }

      updatePlan(result.pricing_plan);
      setStatus(statusNode, "Тариф сохранён.");
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось сохранить тариф.", true);
    }
  });

  deleteButton.addEventListener("click", async () => {
    const confirmed = window.confirm(`Удалить тариф «${plan.name}»? Это действие нельзя отменить.`);
    if (!confirmed) {
      return;
    }

    setStatus(statusNode, "Удаляю тариф...");
    try {
      const response = await fetch(`/api/pricing-plans/${plan.code}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось удалить тариф.");
      }

      pricingPlans = pricingPlans.filter((entry) => entry.code !== plan.code);
      selectedPlanCode = pricingPlans[0]?.code || null;
      render();
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось удалить тариф.", true);
    }
  });
}

async function uploadFile(slug, file, kind, statusNode) {
  setStatus(statusNode, kind === "knowledge" ? "Загружаю базу знаний..." : "Загружаю аватар...");
  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await fetch(`/api/heroes/${slug}/${kind}`, {
      method: "POST",
      body: formData,
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Ошибка загрузки файла.");
    }

    updateHero(result.hero);
    setStatus(statusNode, kind === "knowledge" ? "База знаний обновлена." : "Аватар обновлён.");
  } catch (error) {
    setStatus(statusNode, error.message || "Ошибка загрузки файла.", true);
  }
}

function updateHero(updatedHero) {
  heroes = heroes.map((hero) => hero.slug === updatedHero.slug ? updatedHero : hero);
  render();
}

function prependHero(hero) {
  heroes = [hero, ...heroes];
  selectedSlug = hero.slug;
  render();
}

function updatePlan(updatedPlan) {
  pricingPlans = pricingPlans.map((plan) => plan.code === updatedPlan.code ? updatedPlan : plan);
  render();
}

function prependPlan(plan) {
  pricingPlans = [plan, ...pricingPlans];
  selectedPlanCode = plan.code;
  render();
}

createForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(createForm);
  const payload = {
    name: formData.get("name"),
    emoji: formData.get("emoji"),
  };

  setStatus(createStatus, "Создаю персонажа...");
  try {
    const response = await fetch("/api/heroes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Не удалось создать персонажа.");
    }

    createForm.reset();
    prependHero(result.hero);
    setStatus(createStatus, "Персонаж создан. Теперь можно заполнить его карточку.");
  } catch (error) {
    setStatus(createStatus, error.message || "Не удалось создать персонажа.", true);
  }
});

planCreateForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(planCreateForm);
  const payload = {
    name: formData.get("name"),
    kind: formData.get("kind"),
  };

  setStatus(planCreateStatus, "Создаю тариф...");
  try {
    const response = await fetch("/api/pricing-plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Не удалось создать тариф.");
    }

    planCreateForm.reset();
    prependPlan(result.pricing_plan);
    setStatus(planCreateStatus, "Тариф создан. Теперь можно заполнить его условия.");
  } catch (error) {
    setStatus(planCreateStatus, error.message || "Не удалось создать тариф.", true);
  }
});

function render() {
  renderHeroList();
  renderEditor();
  renderPlanList();
  renderPlanEditor();
}

render();
