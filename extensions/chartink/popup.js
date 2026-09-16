import {chartinkUrl, hostPattern, openalgoOrigin} from './core.js';
const $ = id => document.getElementById(id);
let currentTab;
let currentState = {};
let supported = false;
let connecting = false;
let importing = false;
let refreshId = 0;
const status = text => { $('status').textContent = text; $('status').hidden = !text; };
function updateActions() {
  let sameAddress = false;
  try { sameAddress = openalgoOrigin($('address').value) === currentState.connection?.origin; } catch { /* Connect validates the address. */ }
  const ready = sameAddress && currentState.connection?.status === 'ready';
  $('connection-status').textContent = currentState.connection && !sameAddress
    ? 'Connect this address to use it.' : currentState.connection?.message || '';
  $('connect').disabled = connecting || importing;
  $('connect').textContent = connecting ? 'Connecting…' : sameAddress ? 'Reconnect' : 'Connect';
  $('capture').disabled = connecting || importing || !supported || !ready;
  $('capture').textContent = importing ? 'Opening OpenAlgo…' : 'Research in OpenAlgo';
  $('capture-hint').textContent = !ready ? 'Connect OpenAlgo to continue.' : !supported ? 'Return to your Chartink scanner to import its history.' : '';
  $('resume').disabled = connecting || importing;
  $('discard').disabled = connecting || importing;
  $('new-import').setAttribute('aria-busy', String(importing));
}
async function message(value) {
  const result = await chrome.runtime.sendMessage(value);
  if (!result?.ok) throw new Error(result?.message || 'Unable to contact the extension. Reload it and try again.');
  return result;
}
async function refresh() {
  const requestId = ++refreshId;
  const state = await message({type: 'state'});
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  if (requestId !== refreshId) return;
  currentState = state;
  if (document.activeElement !== $('address')) $('address').value = state.connection?.origin || '';
  $('pending').hidden = !state.pending;
  $('new-import').hidden = Boolean(state.pending);
  $('pending-title').textContent = state.pending?.title || '';
  currentTab = tabs[0];
  supported = false;
  try { chartinkUrl(currentTab?.url); supported = true; } catch { /* Supported adapter only. */ }
  $('open-chartink').hidden = supported;
  updateActions();
  const last = state.lastResult;
  $('last-result').hidden = !last;
  if (last) {
    $('last-result').href = `${openalgoOrigin(last.origin)}/scanner-research?experiment=${encodeURIComponent(last.experimentId)}&view=setup`;
    $('last-result').target = '_blank'; $('last-result').rel = 'noopener';
  }
}
$('address').addEventListener('input', updateActions);
$('connect-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (connecting || importing) return;
  try {
    const origin = openalgoOrigin($('address').value);
    // Permission request stays in the direct user gesture. Only the chosen
    // host is requested, although arbitrary self-hosted addresses are declared.
    const permission = chrome.permissions.request({origins: [hostPattern(origin)]});
    connecting = true; updateActions();
    const accepted = await permission;
    if (!accepted) throw new Error('Allow access to this OpenAlgo address to connect it.');
    await message({type: 'connect', origin});
    status('OpenAlgo is opening. Sign in there if needed.');
    await refresh();
  } catch (error) { status(error.message); }
  finally { connecting = false; updateActions(); }
});
$('capture').addEventListener('click', async () => {
  if ($('capture').disabled) return;
  importing = true; updateActions(); status('Reading Chartink history…');
  try { await message({type: 'capture', tabId: currentTab?.id}); status('Opening your experiment in OpenAlgo…'); }
  catch (error) { status(error.message); }
  finally { importing = false; await refresh().catch(() => {}); updateActions(); }
});
$('resume').addEventListener('click', async () => {
  if ($('resume').disabled) return;
  importing = true; updateActions();
  try { await message({type: 'resume'}); status('Opening the pending import…'); } catch (error) { status(error.message); }
  finally { importing = false; await refresh().catch(() => {}); updateActions(); }
});
$('discard').addEventListener('click', async () => {
  if ($('discard').disabled) return;
  try { await message({type: 'discard'}); status(''); await refresh(); } catch (error) { status(error.message); }
});
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === 'local' || area === 'session') void refresh().catch(() => {});
});
void refresh().catch(error => status(error.message));
