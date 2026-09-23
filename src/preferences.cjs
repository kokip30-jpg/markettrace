'use strict';
const symbol = x => typeof x === 'string' && /^[A-Z0-9.^=-]{1,16}$/.test(x);
function valid(k,v) {
  switch(k) {
    case 'mt_watch_v2': return Array.isArray(v) && v.length<=500 && v.every(symbol);
    case 'mt_compare_v1': return Array.isArray(v) && v.length<=4 && v.every(symbol);
    case 'mt_port_v2': return Array.isArray(v) && v.length<=5000 && v.every(x=>x && symbol(x.ticker) && Number.isFinite(x.qty) && x.qty>0 && Number.isFinite(x.price) && x.price>0 && typeof x.id==='string' && typeof x.date==='string');
    case 'mt_theme': return ['dark','light'].includes(v);
    case 'mt_chart_height': return Number.isFinite(v) && v>=300 && v<=900;
    case 'mt_chart_interval': return ['1','5','15','60','D'].includes(v);
    case 'mt_chart_indicator': return ['','VOL','MA100_200','MA','EMA','BOLL','MACD','RSI','KDJ','OBV'].includes(v);
    default: return false;
  }
}
class Preferences {
  constructor({storage,read,write,status=()=>{}}) {
    Object.assign(this,{storage,read,write,status}); this.user=null; this.values={}; this.pending={}; this.ready=false; this.running=null;
  }
  async init(user) {
    this.user=user;
    if(!user){this.ready=true;return;}
    // Never hydrate an account from a guest's local settings.
    const rows=await this.read(user);
    for(const r of rows) if(valid(r.key,r.value)) this.values[r.key]=r.value;
    try { const saved=JSON.parse(this.storage.getItem(this.queueKey())||'{}'); for(const [k,v] of Object.entries(saved)) if(valid(k,v)) this.pending[k]=v; } catch {}
    Object.assign(this.values,this.pending); this.ready=true;
    this.status(Object.keys(this.pending).length?'Čeká na uložení':'Uloženo v účtu');
  }
  queueKey(){return 'mt_pending_v1:'+this.user;}
  get(k,d) {
    if(this.user) return structuredClone(this.values[k]??d);
    try {const v=JSON.parse(this.storage.getItem(k));return valid(k,v)?v:structuredClone(d);} catch{return structuredClone(d);}
  }
  set(k,v) {
    if(!this.ready || !valid(k,v)) return;
    if(!this.user){try{this.storage.setItem(k,JSON.stringify(v));}catch{this.status('Nastavení nelze uložit v prohlížeči');}return;}
    if(JSON.stringify(this.values[k])===JSON.stringify(v)) return;
    this.values[k]=structuredClone(v); this.pending[k]=structuredClone(v);
    this.persist(); this.status('Ukládám…'); void this.flush();
  }
  persist(){try{this.storage.setItem(this.queueKey(),JSON.stringify(this.pending));}catch{this.status('Místní záloha není dostupná');}}
  async flush() {
    if(!this.user || !this.ready) return true;
    if(this.running) return this.running;
    this.running=(async()=>{
      try {
        while(Object.keys(this.pending).length){
          const batch=structuredClone(this.pending);
          await this.write(this.user,batch);
          for(const [k,v] of Object.entries(batch)) if(JSON.stringify(this.pending[k])===JSON.stringify(v)) delete this.pending[k];
          this.persist();
        }
        this.status('Uloženo v účtu'); return true;
      } catch {this.status('Neuloženo online · zkusit znovu'); return false;}
    })();
    try{return await this.running;} finally{this.running=null;}
  }
  dirty(){return Object.keys(this.pending).length>0;}
}
module.exports={Preferences,valid};
