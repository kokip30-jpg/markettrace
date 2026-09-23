const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Preferences}=require('../src/preferences.cjs');
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v)};};
test('account never imports guest or another account settings',async()=>{
 const s=storage();s.setItem('mt_port_v2',JSON.stringify([{id:'guest',ticker:'AAPL',qty:1,price:20,date:'2026-09-23'}]));
 s.setItem('mt_pending_v1:other',JSON.stringify({mt_theme:'light'}));
 const p=new Preferences({storage:s,read:async()=>[],write:async()=>{}});await p.init('test');
 assert.deepEqual(p.get('mt_port_v2',[]),[]);assert.equal(p.get('mt_theme','dark'),'dark');
});
test('failed save survives restart and retries only for its owner',async()=>{
 const s=storage();let offline=true;const db={};
 const write=async(user,batch)=>{if(offline)throw Error('offline');db[user]={...db[user],...batch};};
 const p=new Preferences({storage:s,read:async()=>[],write});await p.init('a');p.set('mt_theme','light');await p.flush();assert.equal(p.dirty(),true);
 offline=false;const q=new Preferences({storage:s,read:async()=>[],write});await q.init('a');assert.equal(q.get('mt_theme','dark'),'light');await q.flush();assert.deepEqual(db,{a:{mt_theme:'light'}});assert.equal(q.dirty(),false);
});
test('edit during upload is saved after earlier value',async()=>{
 const calls=[];let release;
 const p=new Preferences({storage:storage(),read:async()=>[],write:async(u,b)=>{calls.push(b);if(calls.length===1)await new Promise(r=>release=r);}});
 await p.init('a');p.set('mt_theme','light');p.set('mt_theme','dark');release();await p.flush();
 assert.deepEqual(calls,[{mt_theme:'light'},{mt_theme:'dark'}]);assert.equal(p.dirty(),false);
});
test('failed initial read cannot overwrite remote state',async()=>{
 let writes=0;const p=new Preferences({storage:storage(),read:async()=>{throw Error('offline');},write:async()=>writes++});
 await assert.rejects(p.init('a'));p.set('mt_watch_v2',[]);await p.flush();assert.equal(writes,0);
});
test('remote settings restored; mutable reads cannot silently alter storage',async()=>{
 const p=new Preferences({storage:storage(),read:async()=>[{key:'mt_watch_v2',value:['BMNR']},{key:'mt_chart_interval',value:'60'},{key:'mt_chart_indicator',value:'MA100_200'}],write:async()=>{}});
 await p.init('a');p.get('mt_watch_v2',[]).push('AAPL');assert.deepEqual(p.get('mt_watch_v2',[]),['BMNR']);assert.equal(p.get('mt_chart_interval','5'),'60');assert.equal(p.get('mt_chart_indicator','VOL'),'MA100_200');
});
test('invalid data and unknown keys are rejected',async()=>{
 let writes=0;const p=new Preferences({storage:storage(),read:async()=>[{key:'mt_watch_v2',value:'broken'}],write:async()=>writes++});await p.init('a');
 p.set('mt_chart_interval','invalid');p.set('service_role','secret');assert.deepEqual(p.get('mt_watch_v2',[]),[]);assert.equal(writes,0);
});
