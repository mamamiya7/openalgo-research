import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {runInContext} from 'node:vm';
import {captureChartink} from '../capture.js';
let JSDOM;
try { ({JSDOM}=createRequire(import.meta.url)('jsdom')); }
catch { ({JSDOM}=createRequire(new URL('../../../frontend/package.json',import.meta.url))('jsdom')); }

function page({csv='"Date","Symbol"\r\n"11-09-2026","AAA"\r\n',kind='blob',exportError=false}={}) {
  const dom=new JSDOM('<title>Example scanner, Technical Analysis Scanner</title><button id="today">CSV</button><div id="backtest-container"><div role="combobox"><span class="multiselectcustom__single">9 months</span></div><button id="download">Download</button><button id="history" hidden>CSV</button></div>', {url:'https://chartink.com/screener/example-scanner',runScripts:'outside-only'});
  const w=dom.window;
  w.Blob=Blob;w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;
  Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
  w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}];};
  const revoked=[];
  w.URL.createObjectURL=()=> 'blob:https://chartink.com/test-export';
  w.URL.revokeObjectURL=url=>revoked.push(url);
  const oldCreate=w.URL.createObjectURL, oldClick=w.HTMLAnchorElement.prototype.click, oldDispatch=w.HTMLAnchorElement.prototype.dispatchEvent;
  let today=0, exports=0;
  w.document.querySelector('#today').onclick=()=>{today++;};
  w.document.querySelector('#download').onclick=()=>{w.document.querySelector('#history').hidden=false;};
  w.document.querySelector('#history').onclick=()=>{
    exports++;
    if(exportError) return;
    const a=w.document.createElement('a');a.download='Backtest Example.csv';
    a.href=(kind==='blob'||kind==='dispatch')?w.URL.createObjectURL(new w.Blob([csv],{type:'text/csv;charset=utf-8'})):'data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
    if(kind==='dispatch') a.dispatchEvent(new w.MouseEvent('click'));
    else a.click();
  };
  const clean=()=>{
    assert.equal(w.URL.createObjectURL,oldCreate);assert.equal(w.HTMLAnchorElement.prototype.click,oldClick);assert.equal(w.HTMLAnchorElement.prototype.dispatchEvent,oldDispatch);
    assert.equal(w.__openalgoCsvCaptureActive,undefined);assert.equal(today,0);
  };
  return {dom,w,revoked,clean,get exports(){return exports;},run:()=>runInContext(`(${captureChartink.toString()})()`,dom.getInternalVMContext())};
}

for(const kind of ['blob','data','dispatch']) test('captures official '+kind+' export without touching current results',async()=>{
  const csv='\uFEFF"Date","Time","Symbol"\r\n"11-09-2026","09:15","AAA"\r\n"11-09-2026","09:30","AAA"\r\n';
  const p=page({csv,kind});
  try {const result=await p.run();assert.equal(result.ok,true);assert.equal(result.csv_text,csv);assert.equal(result.source.title,'Example scanner');assert.equal(result.source.selected_period,'9 months');assert.equal(p.exports,1);p.clean();}
  finally{p.dom.window.close();}
});
test('invalid export restores all temporary hooks and returns one error',async()=>{
  const p=page({csv:'Symbol,Price\nAAA,5\n'});
  try{const result=await p.run();assert.equal(result.ok,false);assert.match(result.message,/supported historical/);p.clean();}finally{p.dom.window.close();}
});
test('timeout restores hooks and disconnects observers so later downloads are untouched',async()=>{
  const p=page({exportError:true});const original=p.w.setTimeout;p.w.setTimeout=(fn,ms)=>original(fn,Math.min(ms,20));
  try{const result=await p.run();assert.equal(result.ok,false);assert.match(result.message,/did not arrive/);p.clean();}finally{p.dom.window.close();}
});
test('oversized CSV is rejected before reading its text',async()=>{
  const p=page({csv:'a'.repeat(8*1024*1024+1)});
  try{const result=await p.run();assert.equal(result.ok,false);assert.match(result.message,/8 MiB/);p.clean();}finally{p.dom.window.close();}
});
test('audited real official export is preserved byte for byte when supplied locally', {skip:!process.env.CHARTINK_AUDIT_CSV},async()=>{
  const csv=readFileSync(process.env.CHARTINK_AUDIT_CSV,'utf8');const p=page({csv});
  try{const result=await p.run();assert.equal(result.ok,true);assert.equal(result.csv_text,csv);p.clean();}finally{p.dom.window.close();}
});
