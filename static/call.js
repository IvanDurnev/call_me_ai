const body = document.body;
const websocketUrl = body.dataset.websocketUrl;
const realtimeProvider = body.dataset.realtimeProvider || "openai";
const characterSlug = body.dataset.characterSlug;
const characterName = body.dataset.characterName;
const characterDescription = body.dataset.characterDescription || "";
const startedFrom = body.dataset.startedFrom || "web";
const appUserId = Number(body.dataset.appUserId || 0) || null;
const callAccessAvailable = body.dataset.callAccessAvailable === "true";

const logNode = document.getElementById("call-log");
const micButton = document.getElementById("mic-btn");
const connectionState = document.getElementById("connection-state");
const statusDot = document.getElementById("status-dot");
const callTimerNode = document.getElementById("call-timer");

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

let socket;
let audioContext;
let mediaStream;
let mediaSource;
let processorNode;
let monitorGain;
let currentSource;
let assistantGainNode;
let speaking = false;
let micEnabled = false;
let awaitingResponse = false;
let manuallyClosed = false;
let callActive = false;
let connecting = false;
let bufferedAudioMs = 0;
let turnPrimed = false;
let cancelInFlight = false;
let assistantResponseActive = false;
let responseRequested = false;
let cancelPendingOnCreate = false;
let commitPending = false;
let pendingResponseAfterCommit = false;
let pendingAutoEnd = false;
let speechFrameCount = 0;
let silenceStartedAt = 0;
let interruptLoggedAt = 0;
let callStartedAt = null;
let callTimerInterval = null;
let providerInputSampleRate = realtimeProvider === "elevenlabs" ? 16000 : 24000;
let providerOutputSampleRate = realtimeProvider === "elevenlabs" ? 16000 : 24000;
let callSessionId = null;
let providerConversationId = null;
let finishingCallSession = false;
let localConversationLog = [];
const playbackQueue = [];
const CALL_END_PATTERNS = [
  /\b(пока|прощай|прощайте|до свидания|до встречи|до скорого)\b/,
  /\b(все|всё)[,.! ]*(разговор|достаточно|хватит|закончен|закончим)\b/,
  /\b(давай|давайте)\s+(закончим|заканчивать|завершим|завершать)\b/,
  /\b(можно|давай|давайте)\s+(закончить|завершить)\s+(разговор|звонок)\b/,
  /\b(мне пора|нам пора|пора идти|пора спать)\b/,
  /\b(я (?:пош[её]л|ухож[уы]|отключаюсь)|мне нужно идти)\b/,
];
const SPEECH_THRESHOLD = 0.035;
const INTERRUPT_THRESHOLD = 0.05;
const START_SPEECH_FRAMES = 3;
const INTERRUPT_SPEECH_FRAMES = 3;
const END_SPEECH_MS = 900;
const MIN_TURN_AUDIO_MS = 180;
const INTERRUPT_LOG_COOLDOWN_MS = 1500;
const CHUNK_FADE_MS = 0.008;
let playbackCursorTime = 0;

function logLine(text) {
  const p = document.createElement("p");
  p.className = "log-line";
  p.textContent = text;
  logNode.appendChild(p);
  logNode.scrollTop = logNode.scrollHeight;
}

function logError(text) {
  if (realtimeProvider === "openai" && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "client.error",
      message: text,
    }));
  }
}

function recordConversationLine(role, text, options = {}) {
  const normalizedText = String(text || "").trim();
  if (!normalizedText) {
    return;
  }

  const { replaceLastSameRole = false } = options;
  if (replaceLastSameRole && localConversationLog.length) {
    const lastItem = localConversationLog[localConversationLog.length - 1];
    if (lastItem?.role === role) {
      localConversationLog[localConversationLog.length - 1] = { role, text: normalizedText };
      return;
    }
  }

  localConversationLog.push({ role, text: normalizedText });
  if (localConversationLog.length > 200) {
    localConversationLog = localConversationLog.slice(-200);
  }
}

function normalizeTranscript(text) {
  return (text || "").toLowerCase().replace(/ё/g, "е").trim();
}

function shouldAutoEndCall(text) {
  const normalized = normalizeTranscript(text);
  if (!normalized) {
    return false;
  }
  return CALL_END_PATTERNS.some((pattern) => pattern.test(normalized));
}

function extractAssistantEndCall(text) {
  const raw = String(text || "").trim();
  if (!raw) {
    return { transcript: "", reason: null };
  }

  const marker = "<END_CALL:";
  let transcript = raw;
  let reason = null;

  const markerStart = transcript.lastIndexOf(marker);
  if (markerStart !== -1) {
    const markerEnd = transcript.indexOf(">", markerStart);
    if (markerEnd !== -1) {
      reason = transcript.slice(markerStart + marker.length, markerEnd).trim() || "разговор завершён";
      transcript = `${transcript.slice(0, markerStart)}${transcript.slice(markerEnd + 1)}`.trim();
    }
  }

  const functionMatch = transcript.match(/\b(?:end_call|endcall)\s*\((?:[^()]|\([^)]*\))*\)\s*$/i);
  if (functionMatch) {
    reason = reason || "разговор завершён";
    transcript = transcript.slice(0, functionMatch.index).trim();
  }

  return { transcript, reason };
}

function setMicButtonState(text, disabled = false) {
  micButton.textContent = text;
  micButton.disabled = disabled;
}

function setMicButtonTone(tone) {
  micButton.classList.toggle("call-btn-primary", tone === "primary");
  micButton.classList.toggle("call-btn-danger", tone === "danger");
}

function syncCallControls() {
  if (connecting) {
    setMicButtonState("Соединяем…", true);
    setMicButtonTone("primary");
    return;
  }

  if (!callAccessAvailable) {
    setMicButtonState("Минуты закончились", true);
    setMicButtonTone("danger");
    return;
  }

  if (!callActive) {
    setMicButtonState("Позвонить");
    setMicButtonTone("primary");
    return;
  }

  setMicButtonState("Завершить");
  setMicButtonTone("danger");
}

function setConnectionState(text, live = false) {
  connectionState.textContent = text;
  statusDot.classList.toggle("live", live);
}

function formatCallTimer(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function updateCallTimer() {
  if (!callTimerNode) {
    return;
  }
  if (!callStartedAt) {
    callTimerNode.textContent = "00:00";
    return;
  }
  callTimerNode.textContent = formatCallTimer((Date.now() - callStartedAt) / 1000);
}

function startCallTimer() {
  callStartedAt = Date.now();
  updateCallTimer();
  if (callTimerInterval) {
    window.clearInterval(callTimerInterval);
  }
  callTimerInterval = window.setInterval(updateCallTimer, 1000);
}

function stopCallTimer() {
  if (callTimerInterval) {
    window.clearInterval(callTimerInterval);
    callTimerInterval = null;
  }
}

function base64FromArrayBuffer(arrayBuffer) {
  let binary = "";
  const bytes = new Uint8Array(arrayBuffer);
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return window.btoa(binary);
}

function decodePcm16(base64Audio, sampleRate = providerOutputSampleRate) {
  const binary = window.atob(base64Audio);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  const view = new DataView(bytes.buffer);
  const samples = new Float32Array(bytes.byteLength / 2);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 32768;
  }
  return { samples, sampleRate };
}

function applyChunkEnvelope(samples, sampleRate = providerOutputSampleRate) {
  const framed = new Float32Array(samples);
  const fadeSamples = Math.min(
    Math.floor(sampleRate * CHUNK_FADE_MS),
    Math.floor(framed.length / 2),
  );
  if (fadeSamples <= 0) {
    return framed;
  }

  for (let index = 0; index < fadeSamples; index += 1) {
    const gain = index / fadeSamples;
    framed[index] *= gain;
    framed[framed.length - 1 - index] *= gain;
  }

  return framed;
}

async function playNextChunk() {
  if (speaking || !playbackQueue.length) {
    return;
  }

  speaking = true;
  const { samples: rawSamples, sampleRate } = playbackQueue.shift();
  const samples = applyChunkEnvelope(rawSamples, sampleRate);
  audioContext = audioContext || new AudioContext({ sampleRate });
  assistantGainNode = assistantGainNode || audioContext.createGain();
  assistantGainNode.connect(audioContext.destination);

  const buffer = audioContext.createBuffer(1, samples.length, sampleRate);
  buffer.copyToChannel(samples, 0);

  const source = audioContext.createBufferSource();
  const sourceGain = audioContext.createGain();
  currentSource = source;
  source.buffer = buffer;
  source.connect(sourceGain);
  sourceGain.connect(assistantGainNode);

  const now = audioContext.currentTime;
  const startAt = Math.max(now, playbackCursorTime);
  const duration = buffer.duration;
  const fadeDuration = Math.min(CHUNK_FADE_MS, duration / 2);
  sourceGain.gain.setValueAtTime(0, startAt);
  sourceGain.gain.linearRampToValueAtTime(1, startAt + fadeDuration);
  sourceGain.gain.setValueAtTime(1, startAt + Math.max(0, duration - fadeDuration));
  sourceGain.gain.linearRampToValueAtTime(0, startAt + duration);
  playbackCursorTime = startAt + duration;

  source.onended = async () => {
    if (currentSource === source) {
      currentSource = null;
    }
    if (!playbackQueue.length) {
      playbackCursorTime = 0;
      if (realtimeProvider === "elevenlabs") {
        assistantResponseActive = false;
      }
    }
    speaking = false;
    if (pendingAutoEnd && !playbackQueue.length && !assistantResponseActive) {
      endCall("auto-end");
      return;
    }
    await playNextChunk();
  };
  source.start(startAt);
}

function stopAssistantPlayback() {
  playbackQueue.length = 0;
  playbackCursorTime = 0;
  if (currentSource) {
    currentSource.onended = null;
    try {
      currentSource.stop();
    } catch {
      // Ignore races when the source has already stopped.
    }
    currentSource = null;
  }
  speaking = false;
  assistantResponseActive = false;
}

function interruptAssistant(reason = "voice") {
  const hasAssistantAudio = speaking || playbackQueue.length > 0 || assistantResponseActive || responseRequested;
  if (!hasAssistantAudio) {
    return;
  }

  stopAssistantPlayback();
  pendingResponseAfterCommit = false;

  if (realtimeProvider === "openai") {
    if (socket?.readyState === WebSocket.OPEN && assistantResponseActive) {
      cancelInFlight = true;
      socket.send(JSON.stringify({ type: "response.cancel" }));
    } else if (responseRequested) {
      cancelPendingOnCreate = true;
    }
  }

  if (reason === "voice") {
    const now = Date.now();
    if (now - interruptLoggedAt > INTERRUPT_LOG_COOLDOWN_MS) {
      interruptLoggedAt = now;
      logLine("Перебиваете персонажа, слушаю вас…");
    }
  }
}

function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < float32Array.length; index += 1) {
    let sample = Math.max(-1, Math.min(1, float32Array[index]));
    sample = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(index * 2, sample, true);
  }
  return buffer;
}

function downsampleBuffer(float32Array, inputSampleRate, outputSampleRate) {
  if (outputSampleRate >= inputSampleRate) {
    return float32Array;
  }

  const sampleRateRatio = inputSampleRate / outputSampleRate;
  const newLength = Math.round(float32Array.length / sampleRateRatio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    let accum = 0;
    let count = 0;
    for (let index = offsetBuffer; index < nextOffsetBuffer && index < float32Array.length; index += 1) {
      accum += float32Array[index];
      count += 1;
    }
    result[offsetResult] = accum / count;
    offsetResult += 1;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

function beginVoiceTurn() {
  if (realtimeProvider === "elevenlabs") {
    return;
  }
  turnPrimed = false;
  bufferedAudioMs = 0;
  silenceStartedAt = 0;
  logLine("Слушаю вас…");
}

function requestAssistantReply() {
  if (realtimeProvider !== "openai") {
    return;
  }
  responseRequested = true;
  awaitingResponse = true;
  socket.send(JSON.stringify({
    type: "response.create",
    response: {
      modalities: ["audio", "text"],
      instructions: (
        `РЕЖИМ РОЛИ. Ты именно ${characterName}. `
        + `Описание персонажа: ${characterDescription}. `
        + "Ребёнок разговаривает по телефону со своим любимым персонажем. "
        + "Отвечай только в этой роли, естественно и по-русски. "
        + "Никогда не говори, что ты ИИ, голосовой помощник, программа, бот, модель или сервис. "
        + `Если спрашивают, кто ты, отвечай чётко: ты ${characterName}, `
        + "коротко опиши свой характер и чем можешь помочь ребёнку. "
        + "Нельзя говорить, что твоё имя зависит от пользователя или что тебя можно назвать как угодно."
      ),
    },
  }));
  logLine("Ждём ответ…");
}

function commitVoiceTurn() {
  if (realtimeProvider !== "openai") {
    return;
  }
  commitPending = true;
  socket.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
}

function parseSampleRate(formatValue) {
  const match = String(formatValue || "").match(/(\d{4,6})/);
  return match ? Number(match[1]) : null;
}

async function ensureAudioPipeline() {
  if (processorNode) {
    return;
  }

  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioContext = audioContext || new AudioContext();
  mediaSource = audioContext.createMediaStreamSource(mediaStream);
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);
  monitorGain = audioContext.createGain();
  monitorGain.gain.value = 0;

  processorNode.onaudioprocess = (event) => {
    if (!micEnabled || socket?.readyState !== WebSocket.OPEN) {
      return;
    }

    const input = event.inputBuffer.getChannelData(0);
    let energy = 0;
    for (let index = 0; index < input.length; index += 1) {
      energy += input[index] * input[index];
    }
    const rms = Math.sqrt(energy / input.length);
    const isSpeech = rms >= SPEECH_THRESHOLD;
    const isInterruptSpeech = rms >= INTERRUPT_THRESHOLD;
    const assistantTalkingNow = speaking || playbackQueue.length > 0 || assistantResponseActive || responseRequested;

    if (isSpeech) {
      speechFrameCount += 1;
      silenceStartedAt = 0;
    } else {
      speechFrameCount = 0;
      if (realtimeProvider === "openai") {
        silenceStartedAt = silenceStartedAt || Date.now();
      }
    }

    if (
      isInterruptSpeech &&
      speechFrameCount >= INTERRUPT_SPEECH_FRAMES &&
      (speaking || playbackQueue.length > 0 || awaitingResponse)
    ) {
      interruptAssistant();
    }

    const downsampled = downsampleBuffer(input, audioContext.sampleRate, providerInputSampleRate);
    const pcm16 = floatTo16BitPCM(downsampled);

    if (realtimeProvider === "elevenlabs") {
      socket.send(JSON.stringify({
        user_audio_chunk: base64FromArrayBuffer(pcm16),
      }));
      return;
    }

    const readyToStartTurn = assistantTalkingNow ? isInterruptSpeech : isSpeech;
    if (!turnPrimed && readyToStartTurn && speechFrameCount >= START_SPEECH_FRAMES) {
      beginVoiceTurn();
      turnPrimed = true;
      socket.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
    }

    if (!turnPrimed) {
      return;
    }

    bufferedAudioMs += (downsampled.length / providerInputSampleRate) * 1000;
    socket.send(JSON.stringify({
      type: "input_audio_buffer.append",
      audio: base64FromArrayBuffer(pcm16),
    }));

    if (!isSpeech && silenceStartedAt && Date.now() - silenceStartedAt >= END_SPEECH_MS) {
      finishVoiceTurn();
    }
  };

  mediaSource.connect(processorNode);
  processorNode.connect(monitorGain);
  monitorGain.connect(audioContext.destination);
}

async function enableMicrophone() {
  if (micEnabled) {
    return;
  }

  try {
    await ensureAudioPipeline();
  } catch (error) {
    logLine(`Не удалось получить доступ к микрофону: ${error.message}`);
    return;
  }

  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  micEnabled = true;
  speechFrameCount = 0;
  silenceStartedAt = 0;
  if (callActive) {
    logLine("Микрофон включён. Можете говорить в любой момент.");
  }
}

function finishVoiceTurn() {
  if (realtimeProvider === "elevenlabs") {
    return;
  }

  silenceStartedAt = 0;
  speechFrameCount = 0;
  turnPrimed = false;

  if (bufferedAudioMs < MIN_TURN_AUDIO_MS) {
    bufferedAudioMs = 0;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "input_audio_buffer.clear" }));
    }
    return;
  }

  if (cancelInFlight) {
    pendingResponseAfterCommit = true;
    return;
  }

  commitVoiceTurn();
}

function disableMicrophone() {
  if (!micEnabled) {
    return;
  }

  micEnabled = false;
  bufferedAudioMs = 0;
  turnPrimed = false;
  speechFrameCount = 0;
  silenceStartedAt = 0;
  if (callActive) {
    logLine("Микрофон выключен.");
  }
}

async function startCallSession() {
  finishingCallSession = false;
  providerConversationId = null;
  localConversationLog = [];
  const response = await fetch("/api/call-sessions/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      character_slug: characterSlug,
      started_from: startedFrom,
      app_user_id: appUserId,
      telegram_user_id: tg?.initDataUnsafe?.user?.id ?? null,
      telegram_username: tg?.initDataUnsafe?.user?.username ?? null,
    }),
  });
  const payload = await response.json().catch(() => ({ ok: false, error: "Invalid server response." }));
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "Не удалось создать звонок.");
  }
  callSessionId = payload.call_session_id || null;
  return payload;
}

async function finishCallSession(reason = "client") {
  if (!callSessionId || finishingCallSession || realtimeProvider !== "elevenlabs") {
    return;
  }
  finishingCallSession = true;
  try {
    await fetch(`/api/call-sessions/${callSessionId}/finish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: realtimeProvider,
        conversation_id: providerConversationId,
        reason,
        local_conversation_log: localConversationLog,
      }),
      keepalive: true,
    });
  } catch {
    // Best-effort finish request.
  }
}

function attachCommonSocketHandlers() {
  socket.addEventListener("close", () => {
    connecting = false;
    callActive = false;
    stopCallTimer();
    micEnabled = false;
    bufferedAudioMs = 0;
    turnPrimed = false;
    cancelInFlight = false;
    assistantResponseActive = false;
    responseRequested = false;
    cancelPendingOnCreate = false;
    commitPending = false;
    pendingResponseAfterCommit = false;
    pendingAutoEnd = false;
    setConnectionState("Соединение завершено", false);
    stopAssistantPlayback();
    awaitingResponse = false;
    syncCallControls();
    logLine(manuallyClosed ? "Звонок завершён." : "Соединение оборвалось.");
    void finishCallSession(manuallyClosed ? "manual" : "disconnect");
  });
}

function connectOpenAi() {
  socket = new WebSocket(websocketUrl);

  socket.addEventListener("open", () => {
    connecting = false;
    callActive = true;
    startCallTimer();
    setConnectionState("На линии", true);
    syncCallControls();
    socket.send(JSON.stringify({
      type: "call.start",
      app_user_id: appUserId,
      character_slug: characterSlug,
      telegram_user_id: tg?.initDataUnsafe?.user?.id ?? null,
      telegram_username: tg?.initDataUnsafe?.user?.username ?? null,
      started_from: startedFrom,
    }));
    logLine(`Соединение с ${characterName} установлено.`);
  });

  socket.addEventListener("message", async (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      logError("Получен неожиданный ответ от сервера.");
      return;
    }

    if (payload.type === "call.ready") {
      logLine(`${payload.character.name} готов говорить.`);
      return;
    }

    if (payload.type === "session.created" || payload.type === "session.updated") {
      logLine("Аудиосессия готова.");
      return;
    }

    if (payload.type === "response.audio.delta" && payload.delta) {
      playbackQueue.push(decodePcm16(payload.delta, providerOutputSampleRate));
      assistantResponseActive = true;
      await playNextChunk();
      return;
    }

    if (payload.type === "call.transcript" && payload.transcript) {
      const speaker = payload.role === "user" ? "Вы" : characterName;
      logLine(`${speaker}: ${payload.transcript}`);
      recordConversationLine(payload.role, payload.transcript);
      if (payload.role === "user" && shouldAutoEndCall(payload.transcript)) {
        logLine("Похоже, вы завершили разговор. Закрываю звонок.");
        endCall("auto-end");
      }
      return;
    }

    if (payload.type === "call.end_requested") {
      pendingAutoEnd = true;
      logLine(`${characterName} завершает разговор.`);
      if (!assistantResponseActive && !speaking && !playbackQueue.length) {
        endCall("auto-end");
      }
      return;
    }

    if (payload.type === "response.created") {
      assistantResponseActive = true;
      responseRequested = false;
      if (cancelPendingOnCreate) {
        cancelPendingOnCreate = false;
        cancelInFlight = true;
        socket.send(JSON.stringify({ type: "response.cancel" }));
      }
      return;
    }

    if (payload.type === "input_audio_buffer.committed") {
      commitPending = false;
      if (cancelInFlight || assistantResponseActive) {
        pendingResponseAfterCommit = true;
      } else {
        requestAssistantReply();
        bufferedAudioMs = 0;
      }
      return;
    }

    if (payload.type === "input_audio_buffer.cleared") {
      commitPending = false;
      return;
    }

    if (payload.type === "response.done") {
      assistantResponseActive = false;
      awaitingResponse = false;
      if (cancelInFlight) {
        cancelInFlight = false;
        if (pendingResponseAfterCommit) {
          pendingResponseAfterCommit = false;
          requestAssistantReply();
          bufferedAudioMs = 0;
        }
      }
      if (pendingAutoEnd && !speaking && !playbackQueue.length) {
        endCall("auto-end");
      }
      return;
    }

    if (payload.type === "error") {
      const message = payload.message || payload.error?.message || "unknown";
      awaitingResponse = false;
      responseRequested = false;
      assistantResponseActive = false;
      cancelInFlight = false;
      cancelPendingOnCreate = false;
      if (message.includes("buffer too small")) {
        commitPending = false;
        pendingResponseAfterCommit = false;
        bufferedAudioMs = 0;
      }
      if (message.includes("no active response found")) {
        if (pendingResponseAfterCommit && bufferedAudioMs >= MIN_TURN_AUDIO_MS) {
          pendingResponseAfterCommit = false;
          commitVoiceTurn();
        } else {
          pendingResponseAfterCommit = false;
        }
      }
      syncCallControls();
      logError(message);
    }
  });

  attachCommonSocketHandlers();
}

function connectElevenLabs(sessionPayload) {
  providerConversationId = null;
  socket = new WebSocket(sessionPayload.signed_url);

  socket.addEventListener("open", () => {
    connecting = false;
    callActive = true;
    startCallTimer();
    setConnectionState("На линии", true);
    syncCallControls();
    const initPayload = JSON.stringify(sessionPayload.conversation_initiation_client_data);
    console.log("[ElevenLabs] sending init:", initPayload);
    socket.send(initPayload);
    logLine(`Соединение с ${characterName} установлено.`);
  });

  socket.addEventListener("close", (event) => {
    console.warn("[ElevenLabs] WebSocket closed", event.code, event.reason);
    logLine(`[debug] WS закрыт: code=${event.code} reason=${event.reason || "нет"}`);
  });

  socket.addEventListener("message", async (event) => {
    console.log("[ElevenLabs] message:", event.data.slice(0, 300));
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    const conversationMetadata = payload.conversation_initiation_metadata_event || payload.conversation_initiation_metadata || {};
    providerConversationId = conversationMetadata.conversation_id || payload.conversation_id || providerConversationId;
    const inputRate = parseSampleRate(conversationMetadata.user_input_audio_format);
    const outputRate = parseSampleRate(conversationMetadata.agent_output_audio_format);
    if (inputRate) {
      providerInputSampleRate = inputRate;
    }
    if (outputRate) {
      providerOutputSampleRate = outputRate;
    }

    if (payload.type === "ping") {
      socket.send(JSON.stringify({
        type: "pong",
        event_id: payload.ping_event?.event_id,
      }));
      return;
    }

    if (payload.type === "conversation_initiation_metadata") {
      logLine("Аудиосессия готова.");
      return;
    }

    if (payload.type === "audio" && payload.audio_event?.audio_base_64) {
      playbackQueue.push(decodePcm16(payload.audio_event.audio_base_64, providerOutputSampleRate));
      assistantResponseActive = true;
      await playNextChunk();
      return;
    }

    if (payload.type === "user_transcript" && payload.user_transcription_event?.user_transcript) {
      const transcript = payload.user_transcription_event.user_transcript;
      logLine(`Вы: ${transcript}`);
      recordConversationLine("user", transcript);
      if (shouldAutoEndCall(transcript)) {
        logLine("Похоже, вы завершили разговор. Закрываю звонок.");
        endCall("auto-end");
      }
      return;
    }

    if (payload.type === "agent_response" && payload.agent_response_event?.agent_response) {
      const { transcript, reason } = extractAssistantEndCall(payload.agent_response_event.agent_response);
      if (transcript) {
        logLine(`${characterName}: ${transcript}`);
        recordConversationLine("agent", transcript);
      }
      if (reason) {
        logLine(`${characterName} завершает разговор.`);
        pendingAutoEnd = true;
        if (!assistantResponseActive && !speaking && !playbackQueue.length) {
          endCall("auto-end");
        }
      }
      return;
    }

    if (payload.type === "agent_response_correction" && payload.agent_response_correction_event?.corrected_agent_response) {
      const { transcript, reason } = extractAssistantEndCall(payload.agent_response_correction_event.corrected_agent_response);
      if (transcript) {
        logLine(`${characterName}: ${transcript}`);
        recordConversationLine("agent", transcript, { replaceLastSameRole: true });
      }
      if (reason) {
        logLine(`${characterName} завершает разговор.`);
        pendingAutoEnd = true;
        if (!assistantResponseActive && !speaking && !playbackQueue.length) {
          endCall("auto-end");
        }
      }
      return;
    }

    if (payload.type === "interruption") {
      stopAssistantPlayback();
      return;
    }

    if (payload.type === "error") {
      const message = payload.message || payload.error?.message || "unknown";
      setConnectionState("Ошибка соединения", false);
      logLine(`Ошибка звонка: ${message}`);
    }
  });

  attachCommonSocketHandlers();
}

async function connect() {
  connecting = true;
  manuallyClosed = false;
  finishingCallSession = false;
  syncCallControls();

  if (realtimeProvider === "elevenlabs") {
    const sessionPayload = await startCallSession();
    connectElevenLabs(sessionPayload);
    return;
  }

  connectOpenAi();
}

function endCall(reason = "manual") {
  manuallyClosed = true;
  pendingAutoEnd = false;
  if (socket?.readyState === WebSocket.OPEN) {
    interruptAssistant(reason);
    if (realtimeProvider === "openai") {
      socket.send(JSON.stringify({ type: "call.stop" }));
    }
    socket.close();
    return;
  }

  if (socket?.readyState === WebSocket.CONNECTING) {
    socket.close();
  }
}

micButton.addEventListener("click", async () => {
  if (!callAccessAvailable) {
    logLine("Доступные минуты исчерпаны. Перейдите в личный кабинет.");
    return;
  }

  if (connecting) {
    return;
  }

  if (callActive) {
    endCall("manual");
    return;
  }

  setConnectionState("Запрашиваем доступ к микрофону…", false);
  logLine("Нужен доступ к микрофону, чтобы начать звонок.");

  try {
    await enableMicrophone();
  } catch {
    // Permission errors are already logged in enableMicrophone.
  }

  if (!micEnabled) {
    setConnectionState("Микрофон не разрешён", false);
    syncCallControls();
    return;
  }

  setConnectionState("Соединяем…", false);
  logLine(`Звоним ${characterName}…`);
  try {
    await connect();
  } catch (error) {
    connecting = false;
    syncCallControls();
    setConnectionState("Соединение не удалось", false);
    logLine(error instanceof Error ? error.message : String(error));
    await finishCallSession("setup_failed");
  }
});

setConnectionState("Готов к звонку", false);
updateCallTimer();
syncCallControls();
if (!callAccessAvailable) {
  setConnectionState("Доступные минуты исчерпаны", false);
}
