(() => {
  if (document.getElementById('openalgo-research-action')) return;
  const host = document.createElement('div');
  host.id = 'openalgo-research-action';
  const shadow = host.attachShadow({mode: 'closed'});
  const style = document.createElement('style');
  style.textContent = `:host{position:fixed;right:22px;bottom:22px;z-index:10000;font:14px/1.45 system-ui,sans-serif;max-width:300px}button{font:inherit;background:#143e35;color:#fff;border:1px solid #fff4;border-radius:10px;padding:12px 18px;cursor:pointer;box-shadow:0 4px 18px #0002}button:hover{background:#1c5347}button:focus-visible{outline:3px solid #18a4cf;outline-offset:3px}button:disabled{cursor:progress;opacity:.85}p{margin:8px 0 0;padding:10px 12px;background:#fff;color:#202a27;border:1px solid #ddd;border-radius:8px;box-shadow:0 4px 18px #0001}@media(prefers-reduced-motion:no-preference){button:disabled{animation:pulse 1.8s ease-in-out infinite}@keyframes pulse{50%{opacity:.65}}}`;
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'Research in OpenAlgo';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  status.hidden = true;
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Reading history…';
    status.hidden = true;
    try {
      const result = await chrome.runtime.sendMessage({type: 'capture'});
      if (!result?.ok) throw new Error(result?.message || 'Open the extension and try again.');
      button.textContent = 'Opened in OpenAlgo';
    } catch (error) {
      status.textContent = error.message || 'Open the extension and try again.';
      status.hidden = false;
      button.textContent = 'Research in OpenAlgo';
    } finally { button.disabled = false; }
  });
  shadow.append(style, button, status);
  document.body.append(host);
})();
