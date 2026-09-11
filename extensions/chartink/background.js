import {captureChartink} from './capture.js';
import {allowedRecipient, chartinkUrl, destination, hostPattern, openalgoOrigin, PENDING_TTL, publicState, UUID, validateCapture} from './core.js';

export function createController(api) {
  let mutation = Promise.resolve();
  let capturing = false;
  const serial = action => {
    const next = mutation.then(action, action);
    mutation = next.catch(() => {});
    return next;
  };
  async function pending(prune = true) {
    const {pending: value} = await api.storage.session.get('pending');
    if (value && (!UUID.test(value.id) || Date.now() - value.createdAt > PENDING_TTL)) {
      if (prune) await api.storage.session.remove('pending');
      return null;
    }
    return value || null;
  }
  const ui = sender => sender.id === api.runtime.id && !sender.tab &&
    sender.url === api.runtime.getURL('popup.html');
  const scanner = sender => {
    if (sender.id !== api.runtime.id || sender.frameId !== 0 || !Number.isInteger(sender.tab?.id)) return false;
    try { chartinkUrl(sender.url); return true; } catch { return false; }
  };
  async function permitted(origin) {
    if (!await api.permissions.contains({origins: [hostPattern(origin)]})) {
      throw new Error('Connect your OpenAlgo address from the extension first.');
    }
  }
  async function openPending(value) {
    await permitted(value.origin);
    if (Number.isInteger(value.targetTabId)) {
      try {
        const previous = await api.tabs.get(value.targetTabId);
        if (previous.url === destination(value.origin, value.id)) {
          await api.tabs.update(previous.id, {active: true});
          await tabUpdated(previous.id, {status: 'complete'}, previous);
          return {ok: true, opened: true};
        }
      } catch { /* A closed destination can be safely reopened. */ }
    }
    const tab = await api.tabs.create({url: destination(value.origin, value.id), active: true});
    value.targetTabId = tab.id;
    await api.storage.session.set({pending: value});
    // If a very fast page completed before the persistence above, retry its
    // bridge injection here as well as on tabs.onUpdated. Injection is idempotent.
    const current = await api.tabs.get(tab.id);
    if (current.status === 'complete') await tabUpdated(tab.id, {status: 'complete'}, current);
    return {ok: true, opened: true};
  }
  async function connect(origin) {
    origin = openalgoOrigin(origin);
    await permitted(origin);
    const connection = {origin, status: 'checking', message: 'Checking OpenAlgo…'};
    await api.storage.local.set({connection});
    const tab = await api.tabs.create({url: `${origin}/scanner-research`, active: true});
    await api.storage.session.set({connectionTab: {tabId: tab.id, origin, createdAt: Date.now()}});
    const current = await api.tabs.get(tab.id);
    if (current.status === 'complete') await tabUpdated(tab.id, {status: 'complete'}, current);
    return {ok: true};
  }
  async function start(tabId) {
    if (capturing) throw new Error('History capture is already running.');
    const prior = await pending();
    if (prior) return openPending(prior);
    const {connection} = await api.storage.local.get('connection');
    if (!connection?.origin) throw new Error('Open the extension and connect your OpenAlgo address first.');
    if (connection.status !== 'ready') {
      throw new Error(connection.message || 'Open the extension and finish connecting OpenAlgo first.');
    }
    await permitted(connection.origin);
    const tab = await api.tabs.get(tabId);
    const url = chartinkUrl(tab.url);
    capturing = true;
    try {
      const results = await api.scripting.executeScript({target: {tabId}, world: 'MAIN', func: captureChartink});
      const result = results.find(row => row.frameId === 0)?.result;
      if (!result?.ok) throw new Error(result?.message || 'Chartink history could not be captured.');
      const capture = validateCapture(result, url);
      const value = {id: crypto.randomUUID(), createdAt: Date.now(), targetTabId: null, origin: connection.origin,
        payload: {version: 1, request_id: '', ...capture}};
      value.payload.request_id = value.id;
      // Only one bounded import is kept in session storage, never sync/local.
      // It survives a sleeping service worker but is cleared at browser exit.
      try { await api.storage.session.set({pending: value}); }
      catch { throw new Error('This export is too large to hold in Chrome. Choose a shorter history period and try again.'); }
      return await openPending(value);
    } finally { capturing = false; }
  }
  async function verifyConnection(tabId, value) {
    const results = await api.scripting.executeScript({target: {tabId}, world: 'ISOLATED', func: async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch('/scanner-research/api/imports/chartink/capabilities', {
          credentials: 'same-origin', signal: controller.signal, redirect: 'error',
        });
        if (response.status === 401) return {status: 'sign_in', message: 'Sign in to OpenAlgo, then return to the scanner.'};
        if (!response.ok) return {status: 'incompatible', message: 'Update OpenAlgo Research to a version with the Chartink connector.'};
        const data = await response.json();
        return data.protocol_version === 1 && data.max_csv_bytes === 8388608
          ? {status: 'ready', message: 'Connected to OpenAlgo'}
          : {status: 'incompatible', message: 'This OpenAlgo connector version is not supported.'};
      } catch { return {status: 'unavailable', message: 'OpenAlgo could not be reached. Check that it is running.'}; }
      finally { clearTimeout(timer); }
    }});
    const checked = results.find(row => row.frameId === 0)?.result;
    const {connection} = await api.storage.local.get('connection');
    if (checked && connection?.origin === value.origin) {
      await api.storage.local.set({connection: {origin: value.origin, ...checked}});
      if (checked.status === 'ready') await api.storage.session.remove('connectionTab');
    }
  }
  async function tabUpdated(tabId, change, tab) {
    if (change.status !== 'complete' && !change.url) return;
    const value = await pending(false);
    if (value?.targetTabId === tabId) {
      let url;
      try { url = new URL(tab.url); } catch { return; }
      if (url.origin === value.origin && url.pathname === '/scanner-research' && url.searchParams.get('chartink_import') === value.id) {
        await api.scripting.executeScript({target: {tabId}, files: ['openalgo-bridge.js'], world: 'ISOLATED'});
      }
    }
    const {connectionTab} = await api.storage.session.get('connectionTab');
    if (connectionTab?.tabId === tabId && Date.now() - connectionTab.createdAt <= PENDING_TTL &&
        tab.url && new URL(tab.url).origin === connectionTab.origin) {
      await verifyConnection(tabId, connectionTab);
    }
  }
  async function handle(message, sender) {
    if (!message || typeof message !== 'object') throw new Error('Invalid extension request.');
    if (message.type === 'get-pending' || message.type === 'complete-import') {
      // Read ownership inside the same queue as capture/discard. An old ACK
      // must never clear a replacement capture while storage is awaiting I/O.
      return serial(async () => {
        const value = await pending();
        if (sender.id !== api.runtime.id || !allowedRecipient(sender, value, message.type === 'complete-import' ? message.experimentId : undefined) || message.requestId !== value.id) {
          throw new Error('This tab does not own the pending import.');
        }
        if (message.type === 'get-pending') return {ok: true, payload: value.payload};
        if (!/^[a-f0-9]{32}$/.test(message.experimentId || '')) throw new Error('Invalid saved experiment.');
        await api.storage.local.set({lastResult: {origin: value.origin, experimentId: message.experimentId, title: value.payload.source.title}});
        await api.storage.session.remove('pending');
        return {ok: true};
      });
    }
    if (message.type === 'capture' && scanner(sender)) return serial(() => start(sender.tab.id));
    if (!ui(sender)) throw new Error('Open the extension to use this action.');
    if (message.type === 'state') return serial(async () => {
      const local = await api.storage.local.get(['connection', 'lastResult']);
      return {ok: true, ...publicState(local.connection, await pending(), local.lastResult)};
    });
    if (message.type === 'connect') return serial(() => connect(message.origin));
    if (message.type === 'capture') {
      if (!Number.isInteger(message.tabId)) throw new Error('Open a Chartink scanner first.');
      return serial(() => start(message.tabId));
    }
    if (message.type === 'resume') return serial(async () => {
      const value = await pending();
      if (!value) throw new Error('There is no pending import. Return to the scanner and capture it again.');
      return openPending(value);
    });
    if (message.type === 'discard') return serial(async () => { await api.storage.session.remove('pending'); return {ok: true}; });
    throw new Error('Unknown extension action.');
  }
  return {handle, tabUpdated};
}

if (typeof chrome !== 'undefined' && chrome.runtime?.id) {
  const controller = createController(chrome);
  chrome.storage.session.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'}).catch(() => {});
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    controller.handle(message, sender).then(respond, error => respond({ok: false, message: error.message || 'Import failed. Try again.'}));
    return true;
  });
  chrome.tabs.onUpdated.addListener((id, change, tab) => {
    controller.tabUpdated(id, change, tab).catch(() => {});
  });
}
