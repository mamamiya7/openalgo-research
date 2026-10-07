(() => {
  const id = new URL(location.href).searchParams.get('chartink_import');
  if (!/^[a-f0-9-]{36}$/i.test(id || '')) return;
  globalThis.__openalgoResearchBridge?.dispose();
  let disposed = false;
  let sending = false;
  let acknowledging = false;
  const origin = location.origin;
  const send = value => window.postMessage({version: 1, requestId: id, ...value}, origin);
  const dispose = () => {
    disposed = true;
    clearTimeout(timer);
    window.removeEventListener('message', receive);
    window.removeEventListener('pagehide', dispose);
    if (globalThis.__openalgoResearchBridge?.dispose === dispose) delete globalThis.__openalgoResearchBridge;
  };
  const timer = setTimeout(dispose, 10 * 60 * 1000);
  async function receive(event) {
    if (disposed || event.source !== window || event.origin !== origin ||
        event.data?.version !== 1 || event.data?.requestId !== id) return;
    if (event.data.type === 'openalgo:chartink-ready' && !sending) {
      sending = true;
      try {
        const value = await chrome.runtime.sendMessage({type: 'get-pending', requestId: id});
        if (!disposed) {
          if (value?.ok) send({type: 'openalgo:chartink-import', payload: value.payload});
          else send({type: 'openalgo:chartink-missing'});
        }
      } catch { if (!disposed) send({type: 'openalgo:chartink-missing'}); }
      finally { sending = false; }
    }
    if (event.data.type === 'openalgo:chartink-result' && event.data.ok === true && !acknowledging && /^[a-f0-9]{32}$/.test(event.data.experimentId || '')) {
      // Await acknowledgment before disposal. Navigation can race this event;
      // the server's import retry key still prevents duplicates on recovery.
      acknowledging = true;
      try {
        const result = await chrome.runtime.sendMessage({type: 'complete-import', requestId: id, experimentId: event.data.experimentId});
        if (result?.ok) dispose();
      } catch { /* Keep the bridge and pending payload available for retry. */ }
      finally { acknowledging = false; }
    }
  }
  globalThis.__openalgoResearchBridge = {dispose};
  window.addEventListener('message', receive);
  window.addEventListener('pagehide', dispose, {once: true});
  send({type: 'openalgo:chartink-bridge-ready'});
})();
