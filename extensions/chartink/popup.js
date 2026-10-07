import {chartinkUrl, hostPattern, openalgoOrigin} from './core.js';
const $ = id => document.getElementById(id);
let currentTab;
const status = text => { $('status').textContent = text; $('status').hidden = !text; };
async function message(value) {
  const result = await chrome.runtime.sendMessage(value);
  if (!result?.ok) throw new Error(result?.message || 'Unable to contact the extension. Reload it and try again.');
  return result;
}
async function refresh() {
  const state = await message({type: 'state'});
  if (document.activeElement !== $('address')) $('address').value = state.connection?.origin || '';
  $('connection-status').textContent = state.connection?.message || 'Use the address you normally open to sign in.';
  $('pending').hidden = !state.pending;
  $('new-import').hidden = Boolean(state.pending);
  $('pending-title').textContent = state.pending ? `Pending: ${state.pending.title}` : '';
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  currentTab = tabs[0];
  let supported = false;
  try { chartinkUrl(currentTab?.url); supported = true; } catch { /* Supported adapter only. */ }
  $('capture').disabled = !supported || state.connection?.status !== 'ready';
  $('capture').textContent = supported ? 'Research in OpenAlgo' : 'Open a Chartink scanner';
  const last = state.lastResult;
  $('last-result').hidden = !last;
  if (last) {
    $('last-result').href = `${openalgoOrigin(last.origin)}/scanner-research?experiment=${encodeURIComponent(last.experimentId)}&view=setup`;
    $('last-result').target = '_blank'; $('last-result').rel = 'noopener';
  }
}
$('connect-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const origin = openalgoOrigin($('address').value);
    // Permission request stays in the direct user gesture. Only the chosen
    // host is requested, although arbitrary self-hosted addresses are declared.
    const accepted = await chrome.permissions.request({origins: [hostPattern(origin)]});
    if (!accepted) throw new Error('Allow access to this OpenAlgo address to connect it.');
    await message({type: 'connect', origin});
    status('OpenAlgo is opening. Sign in there if needed.');
    await refresh();
  } catch (error) { status(error.message); }
});
$('capture').addEventListener('click', async () => {
  $('capture').disabled = true; status('Reading Chartink history…');
  try { await message({type: 'capture', tabId: currentTab?.id}); status('Opening your experiment in OpenAlgo…'); }
  catch (error) { status(error.message); }
  finally { await refresh().catch(() => {}); }
});
$('resume').addEventListener('click', async () => {
  try { await message({type: 'resume'}); status('Opening the pending import…'); } catch (error) { status(error.message); }
});
$('discard').addEventListener('click', async () => {
  try { await message({type: 'discard'}); status(''); await refresh(); } catch (error) { status(error.message); }
});
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === 'local' || area === 'session') void refresh().catch(() => {});
});
void refresh().catch(error => status(error.message));
