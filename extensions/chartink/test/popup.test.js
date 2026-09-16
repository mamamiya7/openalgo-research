import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInContext} from 'node:vm';
import {JSDOM} from 'jsdom';
import {chartinkUrl, hostPattern, openalgoOrigin} from '../core.js';

const html = readFileSync(new URL('../popup.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../popup.js', import.meta.url), 'utf8')
  .replace(/^import .* from '\.\/core\.js';\r?\n/, '');
const ready = {origin: 'http://127.0.0.1:5000', status: 'ready', message: 'Connected to OpenAlgo'};
const tick = () => new Promise(resolve => setImmediate(resolve));

function popup(t, {connection = null, pending = null, lastResult = null,
  url = 'https://chartink.com/screener/example-scanner', accepted = true, handle} = {}) {
  const dom = new JSDOM(html, {url: 'https://extension.test/popup.html', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const state = {connection, pending, lastResult};
  const calls = [];
  let listener;
  Object.assign(dom.window, {chartinkUrl, hostPattern, openalgoOrigin, chrome: {
    runtime: {sendMessage: async value => {
      calls.push(structuredClone(value));
      if (value.type === 'state') return {ok: true, ...structuredClone(state)};
      if (handle) {
        const result = await handle(value);
        if (result) return result;
      }
      if (value.type === 'connect') state.connection = {...ready, origin: value.origin};
      if (value.type === 'discard') state.pending = null;
      return {ok: true};
    }},
    permissions: {request: async value => { calls.push({type: 'permission', ...structuredClone(value)}); return accepted; }},
    tabs: {query: async () => [{id: 7, url}]},
    storage: {onChanged: {addListener: value => { listener = value; }}},
  }});
  runInContext(source, dom.getInternalVMContext());
  const element = id => dom.window.document.getElementById(id);
  return {state, calls, element, changed: () => listener({}, 'local'),
    setAddress: value => {
      element('address').value = value;
      element('address').dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    },
    submit: () => element('connect-form').dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true})),
  };
}

test('first visit explains three steps and provides real navigation on unsupported pages', async t => {
  const p = popup(t, {url: 'https://example.com/'});
  await tick();
  assert.equal(p.element('capture').disabled, true);
  p.element('capture').click();
  assert.equal(p.calls.filter(value => value.type === 'capture').length, 0);
  assert.match(p.element('address-help').textContent, /browser address.*port.*No password or API key/);
  assert.equal(p.element('open-chartink').hidden, false);
  assert.equal(p.element('open-chartink').href, 'https://chartink.com/screeners');
  const document = p.element('address').ownerDocument;
  assert.equal(document.querySelectorAll('.step-number').length, 3);
  const guide = [...document.querySelectorAll('footer a')].find(link => link.textContent === 'Setup guide');
  assert.match(guide.href, /extensions\/chartink\/README\.md#install-the-extension$/);
  assert.equal(guide.target, '_blank');
  assert.match(guide.rel, /noopener/);
});

test('a connected scanner can import but a connected unrelated page stays actionable', async t => {
  const scanner = popup(t, {connection: ready});
  const elsewhere = popup(t, {connection: ready, url: 'chrome://newtab/'});
  await tick();
  assert.equal(scanner.element('capture').disabled, false);
  assert.equal(scanner.element('open-chartink').hidden, true);
  assert.equal(scanner.element('connect').textContent, 'Reconnect');
  assert.equal(elsewhere.element('capture').disabled, true);
  assert.equal(elsewhere.element('open-chartink').hidden, false);
  assert.match(elsewhere.element('capture-hint').textContent, /Return to your Chartink scanner/);
  scanner.element('capture').click();
  await tick();
  assert.deepEqual(scanner.calls.find(value => value.type === 'capture'), {type: 'capture', tabId: 7});
  assert.match(scanner.element('status').textContent, /Opening your experiment/);
});

for (const status of ['checking', 'sign_in', 'unavailable', 'incompatible']) {
  test(`${status} connection stays unavailable for capture and offers reconnect`, async t => {
    const p = popup(t, {connection: {...ready, status, message: `Connection: ${status}`}});
    await tick();
    assert.equal(p.element('capture').disabled, true);
    assert.equal(p.element('connect').disabled, false);
    assert.equal(p.element('connect').textContent, 'Reconnect');
    assert.equal(p.element('connection-status').textContent, `Connection: ${status}`);
  });
}

test('pairing requests only the chosen host and sends the exact address including its port', async t => {
  const p = popup(t);
  await tick();
  p.setAddress('http://127.0.0.1:5100/scanner-research');
  p.submit();
  await tick();
  assert.deepEqual(p.calls.find(value => value.type === 'permission'), {type: 'permission', origins: ['http://127.0.0.1/*']});
  assert.deepEqual(p.calls.find(value => value.type === 'connect'), {type: 'connect', origin: 'http://127.0.0.1:5100'});
  assert.equal(p.element('capture').disabled, false);
  assert.equal(p.element('address').value, 'http://127.0.0.1:5100');
});

test('denied host permission does not connect or enable import', async t => {
  const p = popup(t, {accepted: false});
  await tick();
  p.setAddress('http://localhost:5000');
  p.submit();
  await tick();
  assert.equal(p.calls.some(value => value.type === 'connect'), false);
  assert.equal(p.element('capture').disabled, true);
  assert.match(p.element('status').textContent, /Allow access/);
  assert.equal(p.element('connect').disabled, false);
});

test('editing a connected address requires a new connection before sending signals', async t => {
  const p = popup(t, {connection: ready});
  await tick();
  p.setAddress('http://localhost:5200');
  assert.equal(p.element('capture').disabled, true);
  assert.equal(p.element('connect').textContent, 'Connect');
  assert.equal(p.element('connection-status').textContent, 'Connect this address to use it.');
  p.element('capture').click();
  assert.equal(p.calls.some(value => value.type === 'capture'), false);
  p.setAddress(ready.origin);
  assert.equal(p.element('capture').disabled, false);
});

test('a saved connection becoming ready updates capture without reopening the popup', async t => {
  const p = popup(t, {connection: {...ready, status: 'checking'}});
  await tick();
  assert.equal(p.element('capture').disabled, true);
  p.state.connection = ready;
  p.changed();
  await tick();
  assert.equal(p.element('capture').disabled, false);
  assert.equal(p.element('connection-status').textContent, 'Connected to OpenAlgo');
});

test('a pending capture offers recovery and discard without requiring the scanner tab', async t => {
  const p = popup(t, {connection: ready, pending: {title: '<img src=x> My scanner'}, url: 'https://example.com/'});
  await tick();
  assert.equal(p.element('pending').hidden, false);
  assert.equal(p.element('new-import').hidden, true);
  assert.equal(p.element('pending-title').textContent, '<img src=x> My scanner');
  assert.equal(p.element('pending-title').querySelector('img'), null);
  p.element('resume').click();
  await tick();
  assert.equal(p.calls.filter(value => value.type === 'resume').length, 1);
  p.element('discard').click();
  await tick();
  assert.equal(p.state.pending, null);
  assert.equal(p.element('pending').hidden, true);
  assert.equal(p.element('new-import').hidden, false);
  assert.equal(p.element('open-chartink').hidden, false);
});

test('capture remains disabled through storage refresh and duplicate clicks until the operation ends', async t => {
  let finish;
  const held = new Promise(resolve => { finish = resolve; });
  const p = popup(t, {connection: ready, handle: async value => {
    if (value.type === 'capture') { await held; return {ok: false, message: 'Choose a history period first.'}; }
  }});
  await tick();
  p.element('capture').click();
  p.changed();
  await tick();
  assert.equal(p.element('capture').disabled, true);
  p.element('capture').click();
  assert.equal(p.calls.filter(value => value.type === 'capture').length, 1);
  finish();
  await tick();
  assert.equal(p.element('capture').disabled, false);
  assert.match(p.element('status').textContent, /Choose a history period/);
});

test('last saved research opens its exact installation and saved setup', async t => {
  const p = popup(t, {lastResult: {origin: 'https://research.example.com:8443', experimentId: 'a'.repeat(32)}});
  await tick();
  assert.equal(p.element('last-result').hidden, false);
  assert.equal(p.element('last-result').href, `https://research.example.com:8443/scanner-research?experiment=${'a'.repeat(32)}&view=setup`);
  assert.equal(p.element('last-result').target, '_blank');
});
