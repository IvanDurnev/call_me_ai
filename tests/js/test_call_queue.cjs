const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/call.js', 'utf8');

function setup() {
  const calls = {timers: 0, connected: 0, finish: [], statuses: [], buttons: []};
  class Socket {
    static OPEN = 1;
    constructor() { this.listeners = {}; this.readyState = 1; }
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
    send() {}
    async emit(type, payload = {}) {
      for (const handler of this.listeners[type] || []) await handler(payload);
    }
  }
  const context = {
    console: {log() {}, warn() {}}, WebSocket: Socket,
    providerConversationId: null, socket: null, callActive: false, connecting: true,
    callQueued: false, queueTimedOut: false, manuallyClosed: false, callSessionId: 7,
    callConnectedRequest: null, realtimeProvider: 'elevenlabs', characterName: 'Дед Мороз',
    playbackQueue: [], assistantResponseActive: false,
    providerOutputSampleRate: 16000,
    stopRingbackTone() {}, stopAssistantPlayback() { context.playbackQueue.length = 0; },
    startCallTimer() { calls.timers++; }, stopCallTimer() {},
    setConnectionState(text) { calls.statuses.push(text); }, logLine() {},
    updateWaveLiveState() {}, updateWebChatComposerState() {}, isArchivedWebCharacter() { return false; },
    setMicButtonState(text, disabled) { calls.buttons.push({text, disabled}); }, setMicButtonTone() {},
    callAccessAvailable: true, parseSampleRate() { return null; },
    decodePcm16() { return 'audio'; }, async playNextChunk() {},
    fetch() { calls.connected++; return Promise.resolve({ok: true}); },
    finishCallSession(reason) { calls.finish.push(reason); }, loadWebCallHistory() {},
  };
  vm.createContext(context);
  for (const name of ['syncCallControls', 'attachCommonSocketHandlers', 'admitElevenLabsCall', 'connectElevenLabs']) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    vm.runInContext(source.slice(start, next < 0 ? undefined : start + 1 + next), context);
  }
  context.connectElevenLabs({signed_url: 'wss://example.invalid', conversation_initiation_client_data: {}});
  return {context, calls, socket: context.socket,
    message: (data) => context.socket.emit('message', {data: JSON.stringify(data)})};
}

test('hold audio and metadata do not start a call; admission starts it once', async () => {
  const {context, calls, socket, message} = setup();
  await socket.emit('open');
  await message({type: 'conversation_initiation_metadata'});
  await message({type: 'queue_status', queue_status_event: {status: 'waiting'}});
  await message({type: 'audio', audio_event: {audio_base_64: 'AAAA'}});
  assert.equal(calls.timers, 0);
  assert.equal(context.callActive, false);
  assert.equal(calls.buttons.at(-1).text, 'Отменить ожидание');
  assert.equal(calls.buttons.at(-1).disabled, false);
  await message({type: 'queue_status', queue_status_event: {status: 'admitted'}});
  await message({type: 'audio', audio_event: {audio_base_64: 'AAAA'}});
  assert.equal(calls.timers, 1);
  assert.equal(calls.connected, 1);
  assert.equal(context.callActive, true);
  assert.equal(context.callQueued, false);
});

test('immediate conversation starts on activity, without a queue event', async () => {
  const {calls, socket, message} = setup();
  await socket.emit('open');
  assert.equal(calls.timers, 0);
  await message({type: 'audio', audio_event: {audio_base_64: 'AAAA'}});
  assert.equal(calls.timers, 1);
});

test('queue timeout survives socket close and never starts the timer', async () => {
  const {calls, socket, message} = setup();
  await socket.emit('open');
  await message({type: 'queue_status', queue_status_event: {status: 'waiting'}});
  await message({type: 'queue_status', queue_status_event: {status: 'timed_out'}});
  await socket.emit('close', {code: 4300});
  assert.match(calls.statuses.at(-1), /Время ожидания истекло/);
  assert.deepEqual(calls.finish, ['queue_timeout']);
  assert.equal(calls.timers, 0);
});

test('late admission after manual cancellation cannot start the timer', async () => {
  const {context, calls, message} = setup();
  context.manuallyClosed = true;
  await message({type: 'queue_status', queue_status_event: {status: 'admitted'}});
  assert.equal(calls.timers, 0);
  assert.equal(calls.connected, 0);
});
