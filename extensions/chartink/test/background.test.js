import test from 'node:test';
import assert from 'node:assert/strict';
import {createController} from '../background.js';
import {PENDING_TTL} from '../core.js';

function setup() {
  const data={local:{connection:{origin:'http://127.0.0.1:5000',status:'ready'}},session:{}};
  const tabs=new Map([[1,{id:1,url:'https://chartink.com/screener/example',status:'complete'}]]);
  const calls={capture:0,injected:0,created:0};
  const source={url:tabs.get(1).url,title:'Example',selected_period:'9 months',captured_at:new Date().toISOString(),repaints:null,export_kind:'chartink_history_csv'};
  const area=name=>({get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).filter(k=>k in data[name]).map(k=>[k,structuredClone(data[name][k])])),set:async value=>Object.assign(data[name],structuredClone(value)),remove:async keys=>{for(const k of Array.isArray(keys)?keys:[keys])delete data[name][k];}});
  const api={
    runtime:{id:'test-extension',getURL:name=>'chrome-extension://test-extension/'+name},
    storage:{local:area('local'),session:area('session')},permissions:{contains:async()=>true},
    tabs:{get:async id=>{if(!tabs.has(id))throw Error('Closed tab');return tabs.get(id);},create:async value=>{const t={id:++calls.created+10,status:'complete',...value};tabs.set(t.id,t);return t;},update:async(id,change)=>{Object.assign(tabs.get(id),change);return tabs.get(id);}},
    scripting:{executeScript:async options=>{
      if(options.files){calls.injected++;return [];}
      if(options.world==='MAIN'){calls.capture++;return [{frameId:0,result:{ok:true,csv_text:'Date,Symbol\n2026-09-11,AAA\n',source}}];}
      return [{frameId:0,result:{status:'ready',message:'Connected to OpenAlgo'}}];
    }},
  };
  const ui={id:api.runtime.id,url:api.runtime.getURL('popup.html')};
  const controller=createController(api);
  const recipient=()=>({id:api.runtime.id,frameId:0,tab:{id:data.session.pending.targetTabId},url:tabs.get(data.session.pending.targetTabId).url});
  return {api,data,tabs,calls,ui,controller,recipient};
}

test('concurrent user clicks capture once and reuse one pending destination',async()=>{
  const s=setup();
  await Promise.all([s.controller.handle({type:'capture',tabId:1},s.ui),s.controller.handle({type:'capture',tabId:1},s.ui)]);
  assert.equal(s.calls.capture,1);assert.equal(s.calls.created,1);
  assert.equal(s.data.session.pending.payload.request_id,s.data.session.pending.id);
  assert.equal(JSON.stringify(s.data.local).includes('csv_text'),false);
});
test('payload is delivered only to its exact target; acknowledgement clears it',async()=>{
  const s=setup();await s.controller.handle({type:'capture',tabId:1},s.ui);
  const id=s.data.session.pending.id, target=s.recipient();
  await assert.rejects(s.controller.handle({type:'get-pending',requestId:id},{...target,tab:{id:99}}));
  await assert.rejects(s.controller.handle({type:'get-pending',requestId:id},{...target,url:target.url.replace(':5000',':5001')}));
  const result=await s.controller.handle({type:'get-pending',requestId:id},target);
  assert.match(result.payload.csv_text,/AAA/);
  await s.controller.handle({type:'complete-import',requestId:id,experimentId:'a'.repeat(32)},target);
  assert.equal(s.data.session.pending,undefined);assert.equal(s.data.local.lastResult.experimentId,'a'.repeat(32));
});
test('acknowledgement still works if React has already navigated to saved experiment',async()=>{
  const s=setup();await s.controller.handle({type:'capture',tabId:1},s.ui);
  const id=s.data.session.pending.id, sender=s.recipient(), exp='b'.repeat(32);
  sender.url='http://127.0.0.1:5000/scanner-research?experiment='+exp+'&view=setup';
  await assert.rejects(s.controller.handle({type:'get-pending',requestId:id},sender));
  await s.controller.handle({type:'complete-import',requestId:id,experimentId:exp},sender);
  assert.equal(s.data.session.pending,undefined);
});
test('expired pending data is removed without sending it to a tab',async()=>{
  const s=setup();await s.controller.handle({type:'capture',tabId:1},s.ui);
  s.data.session.pending.createdAt=Date.now()-PENDING_TTL-1;
  const state=await s.controller.handle({type:'state'},s.ui);
  assert.equal(state.pending,null);assert.equal(s.data.session.pending,undefined);
});
test('website scripts cannot change the paired host or discard stored imports',async()=>{
  const s=setup();const sender={id:s.api.runtime.id,frameId:0,tab:{id:1},url:s.tabs.get(1).url};
  await assert.rejects(s.controller.handle({type:'connect',origin:'https://example.com'},sender));
  await assert.rejects(s.controller.handle({type:'discard'},sender));
  await assert.rejects(s.controller.handle({type:'capture'}, {...sender,url:'https://chartink.com.evil/screener/x'}));
});
test('quota failure does not open OpenAlgo or leave capture locked',async()=>{
  const s=setup();const set=s.api.storage.session.set;
  s.api.storage.session.set=async()=>{throw Error('QUOTA_BYTES');};
  await assert.rejects(s.controller.handle({type:'capture',tabId:1},s.ui),/shorter history/);
  assert.equal(s.calls.created,0);
  s.api.storage.session.set=set;
  await s.controller.handle({type:'capture',tabId:1},s.ui);
  assert.equal(s.calls.created,1);
});
test('connection verifies protocol through a same-origin capability check',async()=>{
  const s=setup();await s.controller.handle({type:'connect',origin:'http://127.0.0.1:5000'},s.ui);
  assert.equal(s.data.local.connection.status,'ready');assert.equal(s.data.session.connectionTab,undefined);
});

test('an incompatible paired application is rejected before capturing the export',async()=>{
  const s=setup();
  s.data.local.connection.status='incompatible';
  s.data.local.connection.message='Update OpenAlgo Research to a version with the Chartink connector.';
  await assert.rejects(s.controller.handle({type:'capture',tabId:1},s.ui),/Update OpenAlgo Research/);
  assert.equal(s.calls.capture,0);assert.equal(s.calls.created,0);
});

test('delayed acknowledgement cannot remove a replacement capture',async()=>{
  const s=setup();await s.controller.handle({type:'capture',tabId:1},s.ui);
  const id=s.data.session.pending.id, sender=s.recipient();
  const originalSet=s.api.storage.local.set;
  let release, entered;
  const held=new Promise(resolve=>{release=resolve;});
  const started=new Promise(resolve=>{entered=resolve;});
  s.api.storage.local.set=async value=>{
    if(value.lastResult){entered();await held;}
    return originalSet(value);
  };
  const ack=s.controller.handle({type:'complete-import',requestId:id,experimentId:'c'.repeat(32)},sender);
  await started;
  const discard=s.controller.handle({type:'discard'},s.ui);
  const replacement=s.controller.handle({type:'capture',tabId:1},s.ui);
  release();await Promise.all([ack,discard,replacement]);
  assert.ok(s.data.session.pending);
  assert.notEqual(s.data.session.pending.id,id);
  await assert.rejects(s.controller.handle({type:'complete-import',requestId:id,experimentId:'c'.repeat(32)},sender));
  assert.ok(s.data.session.pending);
});
