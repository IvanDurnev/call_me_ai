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

const heroesList = document.getElementById("heroes-list");
const heroEditor = document.getElementById("hero-editor");
const createForm = document.getElementById("hero-create-form");
const createStatus = document.getElementById("hero-create-status");
const plansList = document.getElementById("plans-list");
const planEditor = document.getElementById("plan-editor");
const planCreateForm = document.getElementById("plan-create-form");
const planCreateStatus = document.getElementById("plan-create-status");

if (createForm?.elements?.provider && initialState.realtime_provider) {
  createForm.elements.provider.value = initialState.realtime_provider;
}

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

function selectedHeroDiagnostics() {
  return (initialState.runtime_diagnostics || {})[selectedSlug] || null;
}

function heroProvider(hero) {
  return hero?.provider || initialState.realtime_provider || "openai";
}

function buildAgentSetupChecklist(hero, diagnostics) {
  const lines = [
    `Hero: ${hero.name || hero.slug}`,
    `Provider: ${heroProvider(hero) || "unknown"}`,
    "",
    "Checklist:",
    `1. Agent ID: ${hero.elevenlabs_agent_id || "set in .env (ELEVENLABS_AGENT_ID)"}`,
    `2. Voice ID: ${hero.elevenlabs_voice_id || "missing"}`,
    "3. In ElevenLabs agent Security, allow client overrides for:",
    "   - prompt",
    "   - first_message",
    "   - voice_id",
    "4. Confirm the agent audio formats are compatible with web calls.",
    "5. Run Test agent from the admin panel.",
  ];

  if (diagnostics?.summary) {
    lines.push("", `Current diagnostics: ${diagnostics.summary}`);
  }

  for (const item of diagnostics?.checks || []) {
    lines.push(`- [${String(item.status || "").toUpperCase()}] ${item.label}: ${item.detail}`);
  }

  return lines.join("\n");
}

function setStatus(node, message, isError = false) {
  if (!node) {
    return;
  }
  node.textContent = message;
  node.classList.toggle("is-error", isError);
}

function bindRangeValue(inputId, outputId, precision = 2) {
  const input = document.getElementById(inputId);
  const output = document.getElementById(outputId);
  if (!input || !output) {
    return;
  }
  const renderValue = () => {
    const numeric = Number(input.value);
    output.textContent = Number.isFinite(numeric) ? numeric.toFixed(precision) : input.value;
  };
  input.addEventListener("input", renderValue);
  renderValue();
}

function isValidElevenLabsVoiceId(voiceId) {
  const normalized = String(voiceId || "").trim();
  if (normalized.length < 10 || normalized.length > 128) {
    return false;
  }
  return /^[A-Za-z0-9_-]+$/.test(normalized);
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
  const isElevenLabs = heroProvider(hero) === "elevenlabs";
  const currentVoice = isElevenLabs ? (hero.elevenlabs_voice_id || "") : (hero.voice || "");
  const providerVoices = initialState.voice_options?.[isElevenLabs ? "elevenlabs" : "openai"] || [];
  const options = [...providerVoices];
  if (currentVoice && !options.some((option) => option.value === currentVoice)) {
    options.unshift({
      value: currentVoice,
      label: `${currentVoice} (current)`,
    });
  }
  return options.map((option) => `
    <option value="${escapeHtml(option.value)}" ${option.value === currentVoice ? "selected" : ""}>
      ${escapeHtml(option.label)}${option.recommended ? " ★" : ""}
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

function renderElevenLabsLlmOptions(hero) {
  const currentValue = hero.elevenlabs_llm || "gpt-4o-mini";
  const options = [...(initialState.elevenlabs_llm_options || [])];
  if (currentValue && !options.some((option) => option.value === currentValue)) {
    options.unshift({ value: currentValue, label: `${currentValue} (current)` });
  }
  return options.map((option) => `
    <option value="${escapeHtml(option.value)}" ${option.value === currentValue ? "selected" : ""}>${escapeHtml(option.label)}</option>
  `).join("");
}

function renderElevenLabsTurnEagernessOptions(hero) {
  const currentValue = (hero.elevenlabs_turn_eagerness || "normal").toLowerCase();
  const options = [...(initialState.elevenlabs_turn_eagerness_options || [])];
  if (currentValue && !options.some((option) => option.value === currentValue)) {
    options.unshift({ value: currentValue, label: `${currentValue} (current)` });
  }
  return options.map((option) => `
    <option value="${escapeHtml(option.value)}" ${option.value === currentValue ? "selected" : ""}>${escapeHtml(option.label)}</option>
  `).join("");
}

function buildHeroSavePayload(form, hero) {
  const formData = new FormData(form);
  const provider = formData.get("provider") || heroProvider(hero);
  const payload = {
    slug: formData.get("slug"),
    name: formData.get("name"),
    emoji: formData.get("emoji"),
    description: formData.get("description"),
    provider,
    is_active: formData.get("is_active") === "on",
    greeting_prompt: formData.get("greeting_prompt"),
    system_prompt: formData.get("system_prompt"),
    instructions_override: formData.get("instructions_override"),
    voice: hero.voice || "",
    elevenlabs_voice_id: hero.elevenlabs_voice_id || "",
    elevenlabs_first_message: hero.elevenlabs_first_message || "",
    elevenlabs_agent_id: hero.elevenlabs_agent_id || "",
    elevenlabs_llm: hero.elevenlabs_llm || "gpt-4o-mini",
    elevenlabs_turn_eagerness: hero.elevenlabs_turn_eagerness || "normal",
    realtime_model: hero.realtime_model || "",
    input_transcription_model: hero.input_transcription_model || "",
    input_transcription_language: hero.input_transcription_language || "",
    input_transcription_prompt: hero.input_transcription_prompt || "",
    noise_reduction_type: hero.noise_reduction_type || "none",
    max_output_tokens: hero.max_output_tokens ?? "inf",
    output_audio_speed: hero.output_audio_speed ?? 1,
    mobile_output_gain: hero.mobile_output_gain ?? 3.8,
    desktop_output_gain: hero.desktop_output_gain ?? 1.0,
  };

  payload.mobile_output_gain = formData.has("mobile_output_gain") ? formData.get("mobile_output_gain") : payload.mobile_output_gain;
  payload.desktop_output_gain = formData.has("desktop_output_gain") ? formData.get("desktop_output_gain") : payload.desktop_output_gain;

  if (provider === "elevenlabs") {
    payload.elevenlabs_voice_id = formData.has("elevenlabs_voice_id") ? formData.get("elevenlabs_voice_id") : payload.elevenlabs_voice_id;
    payload.elevenlabs_first_message = formData.has("elevenlabs_first_message") ? formData.get("elevenlabs_first_message") : payload.elevenlabs_first_message;
    payload.elevenlabs_agent_id = formData.has("elevenlabs_agent_id") ? formData.get("elevenlabs_agent_id") : payload.elevenlabs_agent_id;
    payload.elevenlabs_llm = formData.has("elevenlabs_llm") ? formData.get("elevenlabs_llm") : payload.elevenlabs_llm;
    payload.elevenlabs_turn_eagerness = formData.has("elevenlabs_turn_eagerness") ? formData.get("elevenlabs_turn_eagerness") : payload.elevenlabs_turn_eagerness;
    payload.input_transcription_language = formData.has("input_transcription_language") ? formData.get("input_transcription_language") : payload.input_transcription_language;
    payload.output_audio_speed = formData.has("output_audio_speed") ? formData.get("output_audio_speed") : payload.output_audio_speed;
  } else {
    payload.voice = formData.has("voice") ? formData.get("voice") : payload.voice;
    payload.realtime_model = formData.has("realtime_model") ? formData.get("realtime_model") : payload.realtime_model;
    payload.input_transcription_model = formData.has("input_transcription_model") ? formData.get("input_transcription_model") : payload.input_transcription_model;
    payload.input_transcription_language = formData.has("input_transcription_language") ? formData.get("input_transcription_language") : payload.input_transcription_language;
    payload.input_transcription_prompt = formData.has("input_transcription_prompt") ? formData.get("input_transcription_prompt") : payload.input_transcription_prompt;
    payload.noise_reduction_type = formData.has("noise_reduction_type") ? formData.get("noise_reduction_type") : payload.noise_reduction_type;
    payload.max_output_tokens = formData.has("max_output_tokens") ? formData.get("max_output_tokens") : payload.max_output_tokens;
  }

  return payload;
}

function renderEditor() {
  if (!heroEditor) {
    return;
  }
  const hero = selectedHero();
  const diagnostics = selectedHeroDiagnostics();
  const provider = heroProvider(hero);
  const isElevenLabs = provider === "elevenlabs";
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
            <span>Slug</span>
            <input name="slug" type="text" value="${escapeHtml(hero.slug)}" maxlength="64" required pattern="[a-z0-9-]+">
          </label>
          <label class="hero-field">
            <span>Имя</span>
            <input name="name" type="text" value="${escapeHtml(hero.name)}" maxlength="255" required>
          </label>
          <label class="hero-field">
            <span>Эмодзи</span>
            <input name="emoji" type="text" value="${escapeHtml(hero.emoji || "")}" maxlength="16" placeholder="✨">
          </label>
          <label class="hero-field">
            <span>Провайдер звонка</span>
            <select name="provider" id="hero-provider-select">
              <option value="openai" ${provider === "openai" ? "selected" : ""}>OpenAI</option>
              <option value="elevenlabs" ${provider === "elevenlabs" ? "selected" : ""}>ElevenLabs</option>
            </select>
          </label>
        </div>
        <label class="hero-field">
          <span>Описание</span>
          <textarea name="description" rows="4" placeholder="Кто это, как он разговаривает, в чём его характер.">${escapeHtml(hero.description || "")}</textarea>
        </label>
        <label class="hero-field">
          <span>${isElevenLabs ? "Голос (ElevenLabs)" : "Голос (OpenAI)"}</span>
          <select name="${isElevenLabs ? "elevenlabs_voice_id" : "voice"}" id="hero-voice-select">${renderVoiceOptions(hero)}</select>
        </label>
        ${isElevenLabs ? `
          <div class="hero-field">
            <span>Добавить Voice ID в список</span>
            <div class="hero-actions">
              <input name="elevenlabs_voice_id_manual" type="text" value="" placeholder="Например, 21m00Tcm4TlvDq8ikWAM">
              <button class="call-btn call-btn-secondary" type="button" id="hero-add-voice-id-btn">Добавить</button>
            </div>
          </div>
        ` : ""}
        <div class="hero-voice-preview">
          <button class="call-btn call-btn-secondary" type="button" id="voice-preview-btn">Прослушать голос</button>
          <audio id="voice-preview-audio" controls preload="metadata"></audio>
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
          <h3>Общие инструкции</h3>
          <p>Эти настройки работают для обоих провайдеров и определяют поведение героя в разговоре.</p>
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
          <textarea name="instructions_override" rows="4" placeholder="Если заполнить, будет использовано как override поверх базовых инструкций.">${escapeHtml(hero.instructions_override || "")}</textarea>
        </label>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>Громкость воспроизведения</h3>
          <p>Применяется в браузерном звонке: отдельно для мобильных и для десктопных устройств.</p>
        </div>
        <div class="hero-field-grid">
          <label class="hero-field hero-field-wide">
            <span>Mobile gain: <strong id="mobile-output-gain-value">${escapeHtml(Number(hero.mobile_output_gain ?? 3.8).toFixed(2))}</strong></span>
            <input id="mobile-output-gain" name="mobile_output_gain" type="range" min="1" max="6" step="0.1" value="${escapeHtml(hero.mobile_output_gain ?? 3.8)}">
          </label>
          <label class="hero-field hero-field-wide">
            <span>Desktop gain: <strong id="desktop-output-gain-value">${escapeHtml(Number(hero.desktop_output_gain ?? 1.0).toFixed(2))}</strong></span>
            <input id="desktop-output-gain" name="desktop_output_gain" type="range" min="0.5" max="2" step="0.05" value="${escapeHtml(hero.desktop_output_gain ?? 1.0)}">
          </label>
        </div>
      </div>

      <div class="hero-section">
        <div class="hero-section-head">
          <h3>${isElevenLabs ? "ElevenLabs" : "OpenAI"}</h3>
          <p>${isElevenLabs
            ? "Только настройки ElevenLabs: агент, первая фраза, скорость и распознавание."
            : "Только настройки OpenAI realtime: модель, транскрибация, шумодав и лимиты ответа."}</p>
        </div>
        <div class="hero-field-grid">
          ${isElevenLabs ? `
          <label class="hero-field">
            <span>ElevenLabs agent ID</span>
            <input name="elevenlabs_agent_id" type="text" value="${escapeHtml(hero.elevenlabs_agent_id || "")}" placeholder="Опционально, иначе берётся из .env">
          </label>
          <label class="hero-field">
            <span>LLM</span>
            <select name="elevenlabs_llm">${renderElevenLabsLlmOptions(hero)}</select>
          </label>
          <label class="hero-field">
            <span>Чувствительность к шуму / паузам</span>
            <select name="elevenlabs_turn_eagerness">${renderElevenLabsTurnEagernessOptions(hero)}</select>
          </label>
          <label class="hero-field">
            <span>Язык распознавания</span>
            <input name="input_transcription_language" type="text" value="${escapeHtml(hero.input_transcription_language || "")}" placeholder="ru">
          </label>
          <label class="hero-field">
            <span>Скорость голоса</span>
            <input name="output_audio_speed" type="number" min="0.25" max="1.5" step="0.01" value="${escapeHtml(hero.output_audio_speed ?? 1)}">
          </label>
          <label class="hero-field hero-field-wide">
            <span>Первая фраза</span>
            <textarea name="elevenlabs_first_message" rows="2" placeholder="Если задана, ElevenLabs произнесёт её дословно. Если пусто, начало будет генерироваться каждый раз.">${escapeHtml(hero.elevenlabs_first_message || "")}</textarea>
          </label>
          ` : `
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
          <label class="hero-field hero-field-wide">
            <span>Transcription prompt</span>
            <textarea name="input_transcription_prompt" rows="3" placeholder="Подсказка для распознавания речи.">${escapeHtml(hero.input_transcription_prompt || "")}</textarea>
          </label>
          `}
        </div>
      </div>

      ${diagnostics ? `
        <div class="hero-section">
          <div class="hero-section-head">
            <h3>Диагностика</h3>
            <p>${escapeHtml(diagnostics.summary || "Проверка окружения")}</p>
          </div>
          ${isElevenLabs ? `
            <div class="voice-actions">
              <button class="call-btn call-btn-secondary" type="button" id="hero-create-agent-btn">Create agent</button>
              <button class="call-btn call-btn-secondary" type="button" id="hero-test-agent-btn">Test agent</button>
              <button class="call-btn call-btn-secondary" type="button" id="hero-copy-agent-checklist-btn">Copy setup checklist</button>
            </div>
          ` : ""}
          <div class="voice-list">
            ${(diagnostics.checks || []).map((item) => `
              <article class="voice-card">
                <div class="voice-card-head">
                  <div>
                    <h2>${escapeHtml(item.label || "Проверка")}</h2>
                    <p>${escapeHtml(item.detail || "")}</p>
                  </div>
                  <span class="voice-badge${item.status === "ok" ? " voice-badge-ok" : ""}">
                    ${escapeHtml(item.status || "unknown")}
                  </span>
                </div>
              </article>
            `).join("")}
          </div>
        </div>
      ` : ""}

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
  const voiceIdManualInput = form.elements.elevenlabs_voice_id_manual || null;
  const addVoiceIdButton = document.getElementById("hero-add-voice-id-btn");
  const providerSelect = document.getElementById("hero-provider-select");
  const createAgentButton = document.getElementById("hero-create-agent-btn");
  const testAgentButton = document.getElementById("hero-test-agent-btn");
  const copyChecklistButton = document.getElementById("hero-copy-agent-checklist-btn");

  bindRangeValue("mobile-output-gain", "mobile-output-gain-value", 2);
  bindRangeValue("desktop-output-gain", "desktop-output-gain-value", 2);

  providerSelect?.addEventListener("change", () => {
    updateHero({ ...hero, ...buildHeroSavePayload(form, hero) });
  });

  async function persistHeroForm(options = {}) {
    const {
      statusMessage = "Сохраняю настройки героя...",
      rerender = true,
      successMessage = "Настройки героя сохранены.",
    } = options;
    setStatus(statusNode, statusMessage);
    const payload = buildHeroSavePayload(form, hero);

    console.log("[save] payload:", JSON.stringify({
      elevenlabs_first_message: payload.elevenlabs_first_message,
      elevenlabs_llm: payload.elevenlabs_llm,
      elevenlabs_turn_eagerness: payload.elevenlabs_turn_eagerness,
    }));
    const response = await fetch(`/api/heroes/${encodeURIComponent(hero.slug)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Не удалось сохранить героя.");
    }

    updateHero(result.hero, hero.slug);
    selectedSlug = result.hero.slug;
    if (rerender) {
      await reloadAdminData();
      const refreshedStatusNode = document.getElementById("hero-status");
      setStatus(refreshedStatusNode, successMessage);
    }
    return result.hero;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await persistHeroForm();
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
      const response = await fetch(`/api/heroes/${encodeURIComponent(hero.slug)}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось удалить героя.");
      }

      heroes = heroes.filter((entry) => entry.slug !== hero.slug);
      selectedSlug = heroes[0]?.slug || null;
      await reloadAdminData();
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
      if (previewAudioUrl) {
        URL.revokeObjectURL(previewAudioUrl);
        previewAudioUrl = null;
      }
      const response = await fetch("/api/voices/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: selectedVoice, provider }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({ error: "Не удалось получить предпрослушивание." }));
        throw new Error(result.error || "Не удалось получить предпрослушивание.");
      }
      const blob = await response.blob();
      previewAudioUrl = URL.createObjectURL(blob);
      previewAudio.src = previewAudioUrl;
      previewAudio.load();
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
    previewAudio.pause();
    previewAudio.currentTime = 0;
    if (previewAudioUrl) {
      URL.revokeObjectURL(previewAudioUrl);
      previewAudioUrl = null;
    }
    previewAudio.removeAttribute("src");
    previewAudio.load();
    setStatus(statusNode, "Голос обновлён. Нажмите прослушать.", false);
  });

  function addManualVoiceIdToSelect() {
    if (!voiceIdManualInput || !voiceSelect) {
      return;
    }
    const manualVoiceId = String(voiceIdManualInput.value || "").trim();
    if (!manualVoiceId) {
      setStatus(statusNode, "Введите Voice ID, чтобы добавить его в список.", true);
      return;
    }
    if (!isValidElevenLabsVoiceId(manualVoiceId)) {
      setStatus(statusNode, "Некорректный Voice ID. Используйте только буквы, цифры, '_' или '-'.", true);
      return;
    }

    let option = [...voiceSelect.options].find((item) => item.value === manualVoiceId);
    if (!option) {
      option = document.createElement("option");
      option.value = manualVoiceId;
      option.textContent = `${manualVoiceId} (manual)`;
      voiceSelect.prepend(option);
    }
    voiceSelect.value = manualVoiceId;
    voiceIdManualInput.value = "";
    setStatus(statusNode, "Voice ID добавлен в список и выбран. Сохраните героя.", false);
  }

  addVoiceIdButton?.addEventListener("click", addManualVoiceIdToSelect);
  voiceIdManualInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    addManualVoiceIdToSelect();
  });

  testAgentButton?.addEventListener("click", async () => {
    setStatus(statusNode, "Сохраняю и проверяю агента...");
    testAgentButton.disabled = true;
    try {
      await persistHeroForm({
        statusMessage: "Сохраняю настройки перед проверкой агента...",
        rerender: false,
      });
      const response = await fetch(`/api/heroes/${encodeURIComponent(selectedSlug || hero.slug)}/test-agent`, {
        method: "POST",
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось проверить агента.");
      }
      initialState.runtime_diagnostics = initialState.runtime_diagnostics || {};
      initialState.runtime_diagnostics[selectedSlug || hero.slug] = result.diagnostics;
      await reloadAdminData();
      const refreshedStatusNode = document.getElementById("hero-status");
      setStatus(refreshedStatusNode, result.diagnostics?.summary || "Проверка завершена.");
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось проверить агента.", true);
    } finally {
      testAgentButton.disabled = false;
    }
  });

  createAgentButton?.addEventListener("click", async () => {
    setStatus(statusNode, "Сохраняю и создаю ElevenLabs агента...");
    createAgentButton.disabled = true;
    try {
      await persistHeroForm({
        statusMessage: "Сохраняю настройки перед обновлением агента...",
        rerender: false,
      });
      const response = await fetch(`/api/heroes/${encodeURIComponent(selectedSlug || hero.slug)}/create-agent`, {
        method: "POST",
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Не удалось создать агента.");
      }

      if (result.hero) {
        heroes = heroes.map((entry) => entry.slug === result.hero.slug ? result.hero : entry);
      }
      if (result.diagnostics) {
        initialState.runtime_diagnostics = initialState.runtime_diagnostics || {};
        initialState.runtime_diagnostics[selectedSlug || hero.slug] = result.diagnostics;
      }
      await reloadAdminData();

      const refreshedStatusNode = document.getElementById("hero-status");
      const message = result.created
        ? `Agent создан и сохранён: ${result.agent_id}`
        : result.updated
          ? `Agent обновлён: ${result.agent_id}`
          : `Agent уже задан: ${result.agent_id}`;
      setStatus(refreshedStatusNode, message);
    } catch (error) {
      setStatus(statusNode, error.message || "Не удалось создать агента.", true);
    } finally {
      createAgentButton.disabled = false;
    }
  });

  copyChecklistButton?.addEventListener("click", async () => {
    const checklist = buildAgentSetupChecklist(hero, diagnostics);
    try {
      await navigator.clipboard.writeText(checklist);
      setStatus(statusNode, "Чеклист скопирован в буфер обмена.");
    } catch (error) {
      setStatus(statusNode, error?.message || "Не удалось скопировать чеклист.", true);
    }
  });

  knowledgeInput.addEventListener("change", async () => {
    if (!knowledgeInput.files?.length) {
      return;
    }
    await uploadFile(selectedSlug || hero.slug, knowledgeInput.files[0], "knowledge", statusNode);
    knowledgeInput.value = "";
  });

  avatarInput.addEventListener("change", async () => {
    if (!avatarInput.files?.length) {
      return;
    }
    await uploadFile(selectedSlug || hero.slug, avatarInput.files[0], "avatar", statusNode);
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
    const response = await fetch(`/api/heroes/${encodeURIComponent(slug)}/${kind}`, {
      method: "POST",
      body: formData,
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Ошибка загрузки файла.");
    }

    updateHero(result.hero);
    await reloadAdminData();
    setStatus(statusNode, kind === "knowledge" ? "База знаний обновлена." : "Аватар обновлён.");
  } catch (error) {
    setStatus(statusNode, error.message || "Ошибка загрузки файла.", true);
  }
}

function updateHero(updatedHero, previousSlug = null) {
  const sourceSlug = previousSlug || updatedHero.slug;
  heroes = heroes.map((hero) => hero.slug === sourceSlug ? updatedHero : hero);
  if (selectedSlug === sourceSlug) {
    selectedSlug = updatedHero.slug;
  }
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

async function reloadAdminData() {
  const response = await fetch("/api/heroes");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Не удалось обновить данные.");
  }
  heroes = payload.items || [];
  pricingPlans = payload.pricing_plans || [];
  initialState.voice_options = payload.voice_options || [];
  initialState.realtime_model_options = payload.realtime_model_options || [];
  initialState.transcription_model_options = payload.transcription_model_options || [];
  initialState.noise_reduction_options = payload.noise_reduction_options || [];
  initialState.elevenlabs_llm_options = payload.elevenlabs_llm_options || [];
  initialState.elevenlabs_turn_eagerness_options = payload.elevenlabs_turn_eagerness_options || [];
  initialState.runtime_diagnostics = payload.runtime_diagnostics || {};
  if (!heroes.some((hero) => hero.slug === selectedSlug)) {
    selectedSlug = heroes[0]?.slug || null;
  }
  if (!pricingPlans.some((plan) => plan.code === selectedPlanCode)) {
    selectedPlanCode = pricingPlans[0]?.code || null;
  }
  render();
}

createForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(createForm);
  const payload = {
    name: formData.get("name"),
    emoji: formData.get("emoji"),
    provider: formData.get("provider") || initialState.realtime_provider || "openai",
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
    await reloadAdminData();
    const createdMessage = result.agent_created
      ? `Персонаж создан, ElevenLabs agent тоже создан: ${result.agent_id}`
      : "Персонаж создан. Теперь можно заполнить его карточку.";
    setStatus(createStatus, createdMessage);
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

async function bootstrap() {
  if (!heroes.length) {
    try {
      await reloadAdminData();
      return;
    } catch (_error) {
      // Keep the server-rendered empty state if refresh failed.
    }
  }
  render();
}

bootstrap();
