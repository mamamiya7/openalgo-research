import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInContext} from 'node:vm';
import {JSDOM} from 'jsdom';

const source=readFileSync(new URL('../openalgo-bridge.js',import.meta.url),'utf8');
const id='a3d8a143-bf56-4a2b-9cf6-123456789abc';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup() {
  const dom=new JSDOM('',{url:`http://127.0.0.1:5000/scanner-research?chartink_import=${id}`,runScripts:'outside-only'});
  const w=dom.window,calls=[],posts=[];
  w.postMessage=value=>posts.push(value);
  w.chrome={runtime:{sendMessage:async message=>{calls.push(message);return {ok:true,payload:{test:true}};}}};
  const receive=async(data,options={})=>{
    w.dispatchEvent(new w.MessageEvent('message',{data:{version:1,requestId:id,...data},source:w,origin:w.location.origin,...options}));
    await settle();
  };
  const inject=()=>runInContext(source,dom.getInternalVMContext());
  inject();
  return {dom,w,calls,posts,receive,inject};
}

test('bridge handshake waits for the matching same-window recipient',async()=>{
  const s=setup();
  try {
    assert.equal(s.posts[0].type,'openalgo:chartink-bridge-ready');
    await s.receive({type:'openalgo:chartink-ready'},{origin:'http://127.0.0.1:5001'});
    await s.receive({type:'openalgo:chartink-ready'},{source:null});
    await s.receive({type:'openalgo:chartink-ready',requestId:'wrong'});
    assert.equal(s.calls.length,0);
    await s.receive({type:'openalgo:chartink-ready'});
    assert.equal(s.posts.at(-1).type,'openalgo:chartink-import');
    assert.deepEqual(s.posts.at(-1).payload,{test:true});
  } finally {s.dom.window.close();}
});

for(const failure of ['response','exception']) test(`failed ${failure} acknowledgement remains retryable and then cleans up`,async()=>{
  const s=setup();let attempts=0;
  try {
    s.w.chrome.runtime.sendMessage=async()=>{
      attempts++;
      if(attempts===1){if(failure==='exception')throw Error('Worker sleeping');return {ok:false};}
      return {ok:true};
    };
    const result={type:'openalgo:chartink-result',ok:true,experimentId:'a'.repeat(32)};
    await s.receive(result);
    assert.ok(s.w.__openalgoResearchBridge);
    await s.receive(result);
    assert.equal(s.w.__openalgoResearchBridge,undefined);
    await s.receive(result);
    assert.equal(attempts,2);
  } finally {s.dom.window.close();}
});

test('reinjection and page exit remove previous listeners',async()=>{
  const s=setup();
  try {
    s.inject();
    await s.receive({type:'openalgo:chartink-ready'});
    assert.equal(s.calls.length,1);
    s.w.dispatchEvent(new s.w.Event('pagehide'));
    await s.receive({type:'openalgo:chartink-ready'});
    assert.equal(s.calls.length,1);
    assert.equal(s.w.__openalgoResearchBridge,undefined);
  } finally {s.dom.window.close();}
});
