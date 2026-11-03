const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(withVisualViewport = true) {
  const values = {};
  const events = {};
  const frames = [];
  const composer = {};
  const stage = {scrollHeight: 1200, scrollTop: 600, clientHeight: 600};
  const document = {
    documentElement: {style: {setProperty(k, v) { values[k] = v; }}},
    querySelector() { return stage; }, getElementById() { return composer; },
    addEventListener(k, fn) { events[`document:${k}`] = fn; }, activeElement: null,
  };
  const viewport = {height: 740, offsetTop: 0, scale: 1,
    addEventListener(k, fn) { events[`viewport:${k}`] = fn; }};
  const window = {visualViewport: withVisualViewport ? viewport : undefined, innerHeight: 740,
    addEventListener(k, fn) { events[`window:${k}`] = fn; },
    setTimeout(fn) { frames.push(fn); }};
  vm.runInNewContext(fs.readFileSync('static/web-viewport.js', 'utf8'), {
    window, document, requestAnimationFrame(fn) { frames.push(fn); return frames.length; },
  });
  return {values, events, stage, composer, document, viewport, window,
    flush() { while (frames.length) frames.shift()(); }};
}

test('keyboard shrinks and pans viewport, then closing restores full height', () => {
  const s = setup();
  s.document.activeElement = s.composer;
  s.viewport.height = 390;
  s.viewport.offsetTop = 120;
  s.events['viewport:resize']();
  s.events['viewport:scroll']();
  s.flush();
  assert.equal(s.values['--web-viewport-height'], '390px');
  assert.equal(s.values['--web-viewport-top'], '120px');
  assert.equal(s.stage.scrollTop, s.stage.scrollHeight);
  s.document.activeElement = null;
  s.viewport.height = 740;
  s.viewport.offsetTop = 0;
  s.events['document:focusout']();
  s.flush();
  assert.equal(s.values['--web-viewport-height'], '740px');
  assert.equal(s.values['--web-viewport-top'], '0px');
});

test('resizing preserves reading position when composer is not focused', () => {
  const s = setup();
  s.stage.scrollTop = 200;
  s.events['viewport:resize']();
  s.flush();
  assert.equal(s.stage.scrollTop, 200);
});

test('pinch zoom is preserved, and browsers without VisualViewport use innerHeight', () => {
  const s = setup();
  s.viewport.scale = 2;
  s.viewport.height = 370;
  s.events['viewport:resize']();
  s.flush();
  assert.equal(s.values['--web-viewport-height'], '740px');
  const fallback = setup(false);
  fallback.window.innerHeight = 390;
  fallback.events['window:resize']();
  fallback.flush();
  assert.equal(fallback.values['--web-viewport-height'], '390px');
});
