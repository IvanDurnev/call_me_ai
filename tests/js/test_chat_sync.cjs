const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(fetch, storage = new Map()) {
  const context = {window: {}, document: {}, crypto: {randomUUID: () => 'request-0001'}, URL,
    localStorage: {getItem: k => storage.get(k) || null, setItem: (k,v) => storage.set(k,v)}, fetch};
  vm.runInNewContext(fs.readFileSync('static/web-chat-sync.js','utf8'), context);
  return {Client: context.window.ChatSyncClient, storage};
}
const row = (id, text) => ({id:`msg_${id}`, sequence:id, character_slug:'hero', role:'assistant', text, created_at:`2026-11-03T12:00:0${id}Z`});
const reply = payload => ({ok:true, status:200, json:async()=>({ok:true,user_id:1,items:[],cursor:0,requests:[],pending_slugs:[],...payload})});

test('incremental sync keeps the full thread without duplicates and handles pagination', async () => {
  const urls=[];
  const {Client}=setup(async url=>{
    urls.push(url);
    return url.endsWith('=0') ? reply({items:[row(1,'Первое')],cursor:1,has_more:true}) : reply({items:[row(2,'Второе')],cursor:2});
  });
  const client=new Client(1,()=>{});
  await client.sync(); await client.sync();
  assert.equal(client.thread('hero').length,2);
  assert.equal(urls[1],'/api/web-chat/history?after_id=1');
  assert.equal(urls[2],'/api/web-chat/history?after_id=2');
});

test('lost send response keeps the same durable request ID for retry', async () => {
  const sent=[];
  let fail=true,done=false;
  const {Client,storage}=setup(async(url,options)=>{
    if(url==='/api/web-chat') {
      sent.push(JSON.parse(options.body).request_id);
      if(fail) throw new Error('offline');
      done=true; return reply({});
    }
    return reply(done?{items:[row(1,'Ответ')],cursor:1,requests:[{request_id:'request-0001',status:'done'}]}:{});
  });
  const client=new Client(1,()=>{});
  const entry=client.queueMessage('hero','Привет');
  await assert.rejects(client.sendMessage(entry));
  assert.equal(client.outbox.length,1);
  assert.equal(new Client(1,()=>{}).outbox[0].request_id,entry.request_id);
  assert.equal(new Client(2,()=>{}).outbox.length,0);
  fail=false; await client.sendMessage(entry);
  assert.deepEqual(sent,['request-0001','request-0001']);
  assert.equal(client.outbox.length,0);
  assert.equal(client.thread('hero')[0].text,'Ответ');
});

test('a changed browser account clears messages instead of merging another account', async () => {
  const {Client}=setup(async()=>reply({user_id:2,items:[row(1,'Другой аккаунт')]}));
  const client=new Client(1,()=>{}); client.loaded=true;client.messages.set('msg_9',row(9,'Старое'));
  await client.sync();
  assert.equal(client.loaded,false);
  assert.equal(client.messages.size,0);
  assert.match(client.error,/Обновите страницу/);
});

test('server-persisted outgoing messages are not repeated as optimistic bubbles', async()=>{
  const {Client}=setup(async()=>reply({items:[row(1,'Привет')],cursor:1,
    requests:[{request_id:'request-0001',status:'pending',user_message_id:1}],pending_slugs:['hero']}));
  const client=new Client(1,()=>{});client.queueMessage('hero','Привет');await client.sync();
  assert.equal(client.thread('hero').length,1);
  assert.equal(client.pendingSlugs.has('hero'),true);
});
