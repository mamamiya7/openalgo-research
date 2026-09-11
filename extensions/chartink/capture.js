/** Runs only after a user asks for historical export, in the page's MAIN world.
 * Capture the official CSV download rather than rebuilding signals from a
 * rendered chart, internal Vue state, today's stock table or a private endpoint.
 * Every temporary prototype wrapper is restored on success, error and timeout.
 */
export async function captureChartink() {
  const MAX_BYTES = 8 * 1024 * 1024;
  const root = document.querySelector('#backtest-container');
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const canonical = new URL(location.href);
  if (canonical.origin !== 'https://chartink.com' ||
      !/^\/screener\/[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}\/?$/.test(canonical.pathname)) {
    return {ok: false, message: 'Open a Chartink scanner first.'};
  }
  if (!root) return {ok: false, message: 'Open Backtest Results on Chartink, then try again.'};
  const title = normalize(document.title.replace(/,?\s*Technical Analysis Scanner\s*$/i, ''));
  const period = normalize(root.querySelector('[role="combobox"] .multiselectcustom__single')?.textContent ||
    root.querySelector('[role="combobox"]')?.getAttribute('aria-valuetext'));
  if (!title || !period) return {ok: false, message: 'Choose the history period on Chartink first.'};
  if (title.length > 240 || period.length > 80) return {ok: false, message: 'The scanner title or history period exceeds the supported length.'};
  if (globalThis.__openalgoCsvCaptureActive) return {ok: false, message: 'A history capture is already running.'};
  const visible = element => Boolean(element && element.getClientRects().length && !element.disabled && element.getAttribute('aria-disabled') !== 'true');
  const button = label => [...root.querySelectorAll('button')].find(e => normalize(e.textContent) === label && visible(e));
  const source = {
    url: canonical.origin + canonical.pathname.replace(/\/$/, ''), title,
    selected_period: period, captured_at: new Date().toISOString(),
    repaints: /repaint/i.test(root.innerText) ? true : null,
    export_kind: 'chartink_history_csv',
  };
  const oldCreate = URL.createObjectURL;
  const oldClick = HTMLAnchorElement.prototype.click;
  const oldDispatch = HTMLAnchorElement.prototype.dispatchEvent;
  const blobs = new Map();
  const created = new Set();
  let settled = false;
  let capturing = false;
  let finish;
  let timeout;
  let observer;
  let capturedUrl;
  let settleResolve;
  const output = new Promise(resolve => { settleResolve = resolve; });
  const validCsv = text => {
    const header = text.replace(/^\uFEFF/, '').split(/\r?\n/, 1)[0];
    return /(?:^|,)"?(?:date|datetime|timestamp)"?(?:,|$)/i.test(header) &&
      /(?:^|,)"?(?:symbol|ticker)"?(?:,|$)/i.test(header);
  };
  async function captureAnchor(anchor) {
    if (settled || capturing || !/\.csv$/i.test(anchor.download || '')) return false;
    const href = anchor.href;
    const blob = blobs.get(href);
    const dataCsv = /^data:(?:text\/csv|application\/csv|text\/plain)(?:;[^,]*)?,/i.test(href);
    if (!blob && !dataCsv) return false;
    if (blob) capturedUrl = href;
    capturing = true;
    try {
      let text;
      if (blob) {
        if (blob.size > MAX_BYTES) throw new Error('This history exceeds 8 MiB. Choose a shorter period.');
        // Blob.text() strips a UTF-8 BOM. Preserve it so the retained original
        // export hashes exactly like the CSV downloaded manually from Chartink.
        text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(await blob.arrayBuffer());
      } else {
        if (href.length > MAX_BYTES * 4) throw new Error('This history exceeds 8 MiB. Choose a shorter period.');
        const split = href.indexOf(',');
        const body = href.slice(split + 1);
        text = /;base64/i.test(href.slice(0, split))
          ? new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(Uint8Array.from(atob(body), c => c.charCodeAt(0)))
          : decodeURIComponent(body);
      }
      if (new TextEncoder().encode(text).length > MAX_BYTES || !validCsv(text)) {
        throw new Error('Chartink did not return a supported historical signal CSV.');
      }
      // Keep the selected period/URL tied to the export action, not a later edit.
      if (location.origin + location.pathname.replace(/\/$/, '') !== source.url ||
          normalize(root.querySelector('[role="combobox"] .multiselectcustom__single')?.textContent ||
            root.querySelector('[role="combobox"]')?.getAttribute('aria-valuetext')) !== period) {
        throw new Error('The scanner or history period changed during capture. Try again.');
      }
      finish({ok: true, csv_text: text, source});
    } catch (error) {
      finish({ok: false, message: error instanceof Error ? error.message : 'Unable to read this history.'});
    }
    return true;
  }
  function intercept(anchor) {
    if (settled || capturing || !/\.csv$/i.test(anchor.download || '')) return false;
    if (!blobs.has(anchor.href) && !/^data:(?:text\/csv|application\/csv|text\/plain)(?:;[^,]*)?,/i.test(anchor.href)) return false;
    void captureAnchor(anchor);
    return true;
  }
  function create(blob) {
    const url = oldCreate.call(URL, blob);
    if (blob instanceof Blob && created.size < 32) {
      created.add(url);
      if (!blob.type || /^(?:text\/|application\/(?:csv|octet-stream))/i.test(blob.type)) blobs.set(url, blob);
    }
    return url;
  }
  function click(...args) {
    if (intercept(this)) return;
    return oldClick.apply(this, args);
  }
  function dispatch(event) {
    if (event?.type === 'click' && intercept(this)) return false;
    return oldDispatch.call(this, event);
  }
  function captureEvent(event) {
    const anchor = event.target?.closest?.('a[download]');
    if (anchor && intercept(anchor)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }
  finish = result => {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    observer?.disconnect();
    document.removeEventListener('click', captureEvent, true);
    if (URL.createObjectURL === create) URL.createObjectURL = oldCreate;
    if (HTMLAnchorElement.prototype.click === click) HTMLAnchorElement.prototype.click = oldClick;
    if (HTMLAnchorElement.prototype.dispatchEvent === dispatch) HTMLAnchorElement.prototype.dispatchEvent = oldDispatch;
    // Only revoke URLs belonging to the intercepted export; other page assets
    // may still be in use and remain the website's responsibility.
    if (capturedUrl) URL.revokeObjectURL(capturedUrl);
    blobs.clear(); created.clear();
    delete globalThis.__openalgoCsvCaptureActive;
    settleResolve(result);
  };
  try {
    globalThis.__openalgoCsvCaptureActive = true;
    URL.createObjectURL = create;
    HTMLAnchorElement.prototype.click = click;
    HTMLAnchorElement.prototype.dispatchEvent = dispatch;
    document.addEventListener('click', captureEvent, true);
    timeout = setTimeout(() => finish({ok: false, message: 'The history export did not arrive. Wait for Chartink to finish loading, then try again.'}), 15000);
    let clicked = false;
    const exportReady = () => {
      const csv = button('CSV');
      if (csv && !clicked) { clicked = true; observer?.disconnect(); csv.click(); }
    };
    observer = new MutationObserver(exportReady);
    observer.observe(root, {childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'style', 'class']});
    const alreadyOpen = button('CSV');
    if (alreadyOpen) exportReady();
    else {
      const download = button('Download');
      if (!download) throw new Error('Wait for Backtest History to finish loading, then try again.');
      download.click();
      exportReady();
    }
  } catch (error) {
    finish({ok: false, message: error instanceof Error ? error.message : 'Could not capture Chartink history.'});
  }
  return output;
}
