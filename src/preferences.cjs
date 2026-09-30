'use strict';
const symbol = x => typeof x === 'string' && /^[A-Z0-9.^=-]{1,16}$/.test(x);
function valid(k,v) {
  switch(k) {
    case 'mt_watch_v2': return Array.isArray(v) && v.length<=500 && v.every(symbol);
    case 'mt_compare_v1': return Array.isArray(v) && v.length<=4 && v.every(symbol);
    case 'mt_port_v2': return Array.isArray(v) && v.length<=5000 && v.every(x=>x && symbol(x.ticker) && Number.isFinite(x.qty) && x.qty>0 && Number.isFinite(x.price) && x.price>0 && typeof x.id==='string' && typeof x.date==='string');
    case 'mt_theme': return ['dark','light'].includes(v);
    case 'mt_language': return ['cs','en','de','it','fr'].includes(v);
    case 'mt_chart_height': return Number.isFinite(v) && v>=300 && v<=900;
    case 'mt_chart_interval': return ['1','5','15','60','D'].includes(v);
    case 'mt_chart_indicator': return ['','VOL','MA100_200','MA','EMA','BOLL','MACD','RSI','KDJ','OBV'].includes(v);
    case 'mt_watch_groups_v1': return v && typeof v==='object' && Object.keys(v).length<=500 && Object.entries(v).every(([ticker,group])=>symbol(ticker)&&typeof group==='string'&&group.length>0&&group.length<=40);
    case 'mt_stock_notes_v1': return v && typeof v==='object' && Object.keys(v).length<=500 && Object.entries(v).every(([ticker,note])=>symbol(ticker)&&typeof note==='string'&&note.length<=1200);
    case 'mt_decision_log_v1': return Array.isArray(v) && v.length<=200 && v.every(x=>x&&typeof x.id==='string'&&typeof x.ticker==='string'&&symbol(x.ticker||'X')&&['buy','sell','watch','review'].includes(x.type)&&typeof x.note==='string'&&x.note.length>0&&x.note.length<=600&&typeof x.at==='string'&&!Number.isNaN(Date.parse(x.at)));
    case 'mt_trade_plans_v1': return v&&typeof v==='object'&&Object.keys(v).length<=500&&Object.entries(v).every(([ticker,p])=>symbol(ticker)&&p&&typeof p==='object'&&['entry','stop','target'].every(k=>p[k]===null||Number.isFinite(p[k])&&p[k]>0)&&typeof p.thesis==='string'&&p.thesis.length<=600&&typeof p.horizon==='string'&&p.horizon.length<=30&&typeof p.updated==='string');
    case 'mt_portfolio_history_v1': return Array.isArray(v)&&v.length<=365&&v.every(x=>x&&/^\d{4}-\d{2}-\d{2}$/.test(x.date)&&Number.isFinite(x.value)&&x.value>=0&&Number.isFinite(x.spy)&&x.spy>0);
    case 'mt_portfolio_targets_v1': return v&&typeof v==='object'&&Object.keys(v).length<=500&&Object.entries(v).every(([ticker,target])=>symbol(ticker)&&Number.isFinite(target)&&target>=0&&target<=100);
    case 'mt_alerts': return Array.isArray(v) && v.length<=100 && v.every(a=>a&&symbol(a.t)&&['price','rvol','move','score'].includes(a.type||'price')&&['above','below'].includes(a.dir)&&Number.isFinite(a.v)&&a.v>0);
    case 'mt_screener_v1': return v && typeof v==='object' &&
      ['all','unusual','strong-volume','gainers','losers','insider','high-score','near-high','watch','assets'].includes(v.filter) &&
      ['volume','relative','change','score','yearPosition'].includes(v.sort);
    case 'mt_alert_rules_v1': return v && typeof v==='object' &&
      typeof v.enabled==='boolean' && typeof v.watchedOnly==='boolean' &&
      Number.isFinite(v.move) && v.move>=1 && v.move<=25 &&
      Number.isFinite(v.rvol) && v.rvol>=1 && v.rvol<=10 &&
      Number.isFinite(v.insiderValue) && v.insiderValue>=0 && v.insiderValue<=1e9;
    case 'mt_alert_seen_v1': return typeof v==='string' && !Number.isNaN(Date.parse(v));
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
