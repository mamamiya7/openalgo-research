export const MAX_CSV_BYTES = 8 * 1024 * 1024;
export const PENDING_TTL = 60 * 60 * 1000;
export const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

export function chartinkUrl(value) {
  const url = new URL(value);
  if (value.length > 512 || url.origin !== 'https://chartink.com' || url.username || url.password ||
      !/^\/screener\/[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}\/?$/.test(url.pathname)) {
    throw new Error('Open a Chartink scanner to import its history.');
  }
  return url.origin + url.pathname.replace(/\/$/, '');
}

export function openalgoOrigin(value) {
  let url;
  try { url = new URL(value.trim()); } catch { throw new Error('Enter your OpenAlgo address, including http:// or https://.'); }
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '[::1]';
  if (url.username || url.password || !['http:', 'https:'].includes(url.protocol) ||
      (url.protocol === 'http:' && !local)) {
    throw new Error('Use HTTPS for a remote OpenAlgo instance, or HTTP on localhost.');
  }
  return url.origin;
}

// Chrome host permissions may cover multiple ports; the bridge additionally
// checks the exact configured origin and the particular destination tab.
export function hostPattern(origin) {
  const url = new URL(openalgoOrigin(origin));
  return `${url.protocol}//${url.hostname}/*`;
}

export function validateCapture(value, expectedUrl) {
  if (!value || typeof value.csv_text !== 'string' || !value.csv_text ||
      new TextEncoder().encode(value.csv_text).length > MAX_CSV_BYTES) {
    throw new Error('Chartink history must be a non-empty CSV of at most 8 MiB.');
  }
  const source = value.source;
  if (!source || chartinkUrl(source.url) !== chartinkUrl(expectedUrl) ||
      typeof source.title !== 'string' || !source.title.trim() || source.title.length > 240 ||
      typeof source.selected_period !== 'string' || !source.selected_period.trim() || source.selected_period.length > 80 ||
      typeof source.captured_at !== 'string' || source.captured_at.length > 40 || !/(Z|[+-]\d{2}:\d{2})$/.test(source.captured_at) || !Number.isFinite(Date.parse(source.captured_at)) ||
      ![null, true, false].includes(source.repaints) || source.export_kind !== 'chartink_history_csv') {
    throw new Error('The scanner history could not be identified. Reload Chartink and try again.');
  }
  return {csv_text: value.csv_text, source: {...source, url: chartinkUrl(source.url)}};
}

export function allowedRecipient(sender, pending, completedExperiment) {
  if (!pending || sender.frameId !== 0 || sender.tab?.id !== pending.targetTabId) return false;
  try {
    const url = new URL(sender.url);
    return url.origin === pending.origin && url.pathname === '/scanner-research' &&
      (url.searchParams.get('chartink_import') === pending.id ||
        (typeof completedExperiment === 'string' && /^[a-f0-9]{32}$/.test(completedExperiment) &&
          url.searchParams.get('experiment') === completedExperiment));
  } catch { return false; }
}

export function destination(origin, requestId) {
  if (!UUID.test(requestId)) throw new Error('Invalid import identity.');
  return `${openalgoOrigin(origin)}/scanner-research?chartink_import=${requestId}`;
}

export function publicState(connection, pending, lastResult) {
  return {
    connection: connection || null,
    pending: pending ? {id: pending.id, title: pending.payload.source.title, createdAt: pending.createdAt} : null,
    lastResult: lastResult || null,
  };
}
