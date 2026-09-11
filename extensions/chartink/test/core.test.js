import test from 'node:test';
import assert from 'node:assert/strict';
import {allowedRecipient, chartinkUrl, destination, hostPattern, MAX_CSV_BYTES, openalgoOrigin, publicState, validateCapture} from '../core.js';

const id = 'de9c8411-a7fb-4b21-8c37-c53bdfb69150';
const origin = 'http://127.0.0.1:5000';
const scanner = 'https://chartink.com/screener/example-scanner';
const capture = () => ({csv_text:'Date,Symbol\r\n11-09-2026,AAA\r\n', source:{url:scanner,title:'Example scanner',selected_period:'9 months',captured_at:'2026-09-11T06:00:00.000Z',repaints:null,export_kind:'chartink_history_csv'}});

test('scanner identity accepts one exact origin and screener path', () => {
  assert.equal(chartinkUrl(scanner + '/?view=stocks'), scanner);
  for (const url of ['https://chartink.com.evil/screener/a','https://evil/?chartink.com/screener/a','http://chartink.com/screener/a','https://chartink.com:8443/screener/a','https://chartink.com/screener/a/b']) assert.throws(() => chartinkUrl(url));
});
test('connection retains exact port, accepts a pasted app path, and avoids credentials', () => {
  assert.equal(openalgoOrigin(origin + '/scanner-research?job=123'), origin);
  assert.equal(hostPattern(origin), 'http://127.0.0.1/*');
  assert.equal(openalgoOrigin('https://research.example.com/app'), 'https://research.example.com');
  for (const value of ['file:///etc/passwd','http://public.example.com','https://user:pass@example.com','javascript:alert(1)','not a url']) assert.throws(() => openalgoOrigin(value));
});
test('capture retains original CSV bytes and requires history metadata', () => {
  const value = capture(); value.csv_text = '\uFEFF"Date","Symbol"\r\n"11-09-2026","AAA"\r\n';
  assert.equal(validateCapture(value, scanner).csv_text, value.csv_text);
  assert.throws(() => validateCapture({...value,source:{...value.source,export_kind:'current_stocks'}}, scanner));
  assert.throws(() => validateCapture({...value,source:{...value.source,url:'https://chartink.com/screener/other'}}, scanner));
  assert.throws(() => validateCapture({...value,csv_text:'é'.repeat(MAX_CSV_BYTES/2+1)},scanner));
});
test('only the selected destination tab and exact origin can retrieve an import', () => {
  const pending = {id,origin,targetTabId:12};
  const sender = {tab:{id:12},frameId:0,url:destination(origin,id)};
  assert.equal(allowedRecipient(sender,pending),true);
  assert.equal(allowedRecipient({...sender,tab:{id:13}},pending),false);
  assert.equal(allowedRecipient({...sender,frameId:1},pending),false);
  assert.equal(allowedRecipient({...sender,url:destination('http://127.0.0.1:5001',id)},pending),false);
  const result = 'a'.repeat(32);
  const navigated = {...sender,url:origin+'/scanner-research?experiment='+result};
  assert.equal(allowedRecipient(navigated,pending),false);
  assert.equal(allowedRecipient(navigated,pending,result),true);
});
test('popup state contains no pending CSV or account credentials', () => {
  const state=publicState({origin,status:'ready'},{id,payload:capture(),createdAt:5},null);
  assert.equal(state.pending.title,'Example scanner');
  assert.equal(JSON.stringify(state).includes('csv_text'),false);
  assert.throws(() => destination(origin,'../login'));
});
