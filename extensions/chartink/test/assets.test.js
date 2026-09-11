import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {hostPattern} from '../core.js';

const root = new URL('../', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('manifest.json', root), 'utf8'));

test('every supported local HTTP connection has an optional permission and public HTTP does not', () => {
  for (const origin of ['http://localhost:5000', 'http://127.0.0.1:5000', 'http://[::1]:5000']) {
    assert.ok(manifest.optional_host_permissions.includes(hostPattern(origin)));
  }
  assert.ok(!manifest.optional_host_permissions.includes('http://*/*'));
  assert.throws(() => hostPattern('http://public.example.com'));
});

test('popup logo and privacy assets resolve inside the extension without remote code', () => {
  for (const name of ['popup.html', 'privacy.html']) {
    const dom = new JSDOM(readFileSync(new URL(name, root), 'utf8'));
    try {
      const {document} = dom.window;
      for (const element of document.querySelectorAll('img[src], script[src], link[rel="stylesheet"]')) {
        const path = element.getAttribute('src') || element.getAttribute('href');
        assert.ok(!/^(https?:)?\/\//.test(path), path);
        assert.ok(existsSync(new URL(path, root)), path);
      }
      if (name === 'popup.html') {
        assert.ok(document.querySelector('header img[width="40"][height="40"]'));
        assert.equal(document.querySelector('footer a').getAttribute('href'), 'privacy.html');
      } else {
        assert.equal(document.querySelectorAll('script').length, 0);
        assert.match(document.body.textContent, /one hour/);
        assert.match(document.body.textContent, /does not delete imports already saved in OpenAlgo/);
      }
    } finally { dom.window.close(); }
  }
});
