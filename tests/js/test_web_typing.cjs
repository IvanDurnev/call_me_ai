const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/call.js', 'utf8');

function setup() {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const previews = ['Первый герой', 'Второй герой'].map(textContent => ({textContent, dataset: {}}));
  const histories = new Map();
  const context = {
    characterSlug: 'first', webChatSending: false, webChatSendingSlug: null,
    callActive: false, connecting: false, hasWebTextChat: true, remoteTypingSlugs: new Set(), webChatSendError: '', chatSyncRevision: -1,
    webCharacters: [{slug:'first'}, {slug:'second'}], renderChatSyncNotice() {},
    legacyImportRunning: false, readLegacyChatHistories: () => null, legacyChatHistories: null,
    webChatInputNode: {value: 'Привет', focus() {}},
    webCharacterCardNodes: previews.map((preview, i) => ({
      dataset: {characterSlug: i === 0 ? 'first' : 'second'}, querySelector() { return preview; },
    })),
    webChatHistories: histories, currentWebChatHistory() { return []; },
    normalizeStoredHistory: x => x, persistWebChatHistories() {},
    renderWebChatIntro() {}, updateWebChatComposerState() {},
    setConnectionState(text) { context.header = text; }, fetch() { return pending; },
  };
  context.chatSyncClient = {
    loaded: true, pendingSlugs: new Set(), revision: 0, outbox: [],
    queueMessage(slug,text) { const entry={request_id:'request-1',character_slug:slug,text};this.outbox.push(entry);this.revision++;return entry; },
    thread(slug) { return histories.get(slug) || []; },
    async sendMessage(entry) {
      const response = await pending;
      if (response.error) throw new Error(response.error);
      histories.set(entry.character_slug, [{role:'assistant',text:'Здравствуйте'}]);this.revision++;
    },
  };
  context.defaultChatHistory = () => [];
  vm.createContext(context);
  for (const name of ['setCurrentWebChatHistory', 'setIdleConnectionState', 'setWebCharacterTyping', 'applyChatSyncState', 'sendWebChatMessage']) {
    const pattern = new RegExp(`(?:async )?function ${name}\\(`);
    const start = source.search(pattern);
    const next = source.slice(start + 1).search(/\n(?:async )?function /);
    vm.runInContext(source.slice(start, start + 1 + next), context);
  }
  return {context, previews, histories, resolve};
}

test('typing is shown only for the replying hero, and reply stays in original chat after switching', async () => {
  const s = setup();
  const task = s.context.sendWebChatMessage();
  assert.equal(s.previews[0].textContent, 'Печатает...');
  assert.equal(s.previews[1].textContent, 'Второй герой');
  s.context.characterSlug = 'second';
  s.context.setIdleConnectionState();
  assert.equal(s.context.header, '');
  s.resolve({ok: true, json: async () => ({ok: true, message: {text: 'Здравствуйте'}})});
  await task;
  assert.equal(s.previews[0].textContent, 'Первый герой');
  assert.equal(s.histories.get('first').at(-1).text, 'Здравствуйте');
  assert.deepEqual(Array.from(s.histories.get('second')), []);
});

test('failed requests restore the hero preview and clear typing state', async () => {
  const s = setup();
  const task = s.context.sendWebChatMessage();
  s.resolve({error: 'Попробуйте позже'});
  await task;
  assert.equal(s.previews[0].textContent, 'Первый герой');
  assert.equal(s.context.webChatSending, false);
  assert.equal(s.context.webChatSendingSlug, null);
  assert.equal(s.context.header, '');
});
