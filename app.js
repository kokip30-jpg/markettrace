(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const STORE = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
  };
  const state = {
    market: [], marketMeta: {}, byTicker: new Map(),
    watch: STORE.get('mt_watch_v2', ['NVDA','AAPL','MSFT','AMZN','TSLA','AMD']),
    portfolio: STORE.get('mt_port_v2', []),
    view: 'overview', selected: null, insiderMode: 'buys', insiderData: {}, gurus: [], chartInterval: '5', detailBars: [],
  };
  const cache = new Map();
  const esc = v => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const ok = v => Number.isFinite(Number(v));
  const money = v => ok(v) ? new Intl.NumberFormat('cs-CZ',{style:'currency',currency:'USD',minimumFractionDigits:Math.abs(v)<1?4:2,maximumFractionDigits:Math.abs(v)<1?4:2}).format(v) : '—';
  const num = (v,d=2) => ok(v) ? new Intl.NumberFormat('cs-CZ',{maximumFractionDigits:d,minimumFractionDigits:d}).format(v) : '—';
  const compact = v => ok(v) ? new Intl.NumberFormat('cs-CZ',{notation:'compact',maximumFractionDigits:1}).format(v) : '—';
  const pct = v => ok(v) ? `${Number(v)>0?'+':''}${num(v,2)} %` : '—';
  const cls = v => Number(v)>0?'up':Number(v)<0?'down':'flat';
  const date = v => v ? new Date(v).toLocaleDateString('cs-CZ') : '—';
  const dateTime = v => v ? new Date(v).toLocaleString('cs-CZ',{day:'numeric',month:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
  const rel = v => { const n=Number(v); return Number.isFinite(n)?`${num(n,2)}×`:'—'; };
  const signalClass = s => s>=75?'high':s>=50?'mid':'';
  const toast = text => { const el=$('#toast'); el.textContent=text; el.classList.add('show'); clearTimeout(toast.t); toast.t=setTimeout(()=>el.classList.remove('show'),2200); };
  async function data(name, fresh = false) {
    if (!fresh && cache.has(name)) return cache.get(name);
    const url = `data/${name}?v=${Math.floor(Date.now()/300000)}`;
    const p = fetch(url,{cache:'no-cache'}).then(r => { if(!r.ok) throw new Error(`${name}: ${r.status}`); return r.json(); });
    cache.set(name,p); try { return await p; } catch(e) { cache.delete(name); throw e; }
  }

  function setView(view) {
    state.view=view;
    $$('.view').forEach(el=>el.classList.toggle('active',el.id===`view-${view}`));
    $$('.main-nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
    if(view==='insiders') loadInsiders(); if(view==='gurus') loadGurus(); if(view==='portfolio') renderPortfolio();
    if(view!=='detail') location.hash = view==='overview'?'prehled':view;
    window.scrollTo({top:0,behavior:'smooth'});
  }
  $$('.main-nav button').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
  $$('[data-jump]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.jump)));
  $('#backBtn').addEventListener('click',()=>setView('overview'));

  function saveWatch(){ state.watch=[...new Set(state.watch)]; STORE.set('mt_watch_v2',state.watch); renderWatch(); renderStocks(); }
  function toggleWatch(ticker){ ticker=ticker.toUpperCase(); const i=state.watch.indexOf(ticker); if(i>=0){state.watch.splice(i,1);toast(`${ticker} odebrána ze sledovaných`);}else{state.watch.push(ticker);toast(`${ticker} přidána do sledovaných`);} saveWatch(); if(state.selected===ticker) renderDetailWatch(); }
  function renderWatch(){
    const box=$('#watchlist'); if(!state.watch.length){box.innerHTML='<div class="empty-state">Watchlist je prázdný.</div>';return;}
    box.innerHTML=state.watch.map(t=>{const s=state.byTicker.get(t);return `<div class="watch-row"><button class="open-stock" data-open="${esc(t)}"><b>${esc(t)}</b><small>${esc(s?.name||'Čekám na data')}</small></button><div class="quote-mini"><b>${money(s?.price)}</b><span class="${cls(s?.change)}">${pct(s?.change)}</span></div><button class="remove-watch" data-unwatch="${esc(t)}" aria-label="Odebrat ${esc(t)}">×</button></div>`;}).join('');
  }
  $('#addWatchBtn').addEventListener('click',()=>{const s=$('#sideSearch');s.hidden=!s.hidden;if(!s.hidden)$('#watchInput').focus();});
  function addWatchInput(){const t=$('#watchInput').value.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,'');if(!t)return;if(!state.byTicker.has(t)){toast('Tento ticker zatím není v tržním přehledu');return;}if(!state.watch.includes(t))state.watch.push(t);$('#watchInput').value='';saveWatch();}
  $('#watchAddConfirm').addEventListener('click',addWatchInput); $('#watchInput').addEventListener('keydown',e=>{if(e.key==='Enter')addWatchInput();});

  function scoreStock(s){ let score=45; const rv=Number(s.rel_volume)||0, ch=Math.abs(Number(s.change)||0); if(rv>1)score+=Math.min(25,(rv-1)*24);score+=Math.min(15,ch*2);if(s.insider_buys)score+=Math.min(15,s.insider_buys*4);return Math.max(0,Math.min(100,Math.round(score))); }
  function renderMetrics(){
    const rows=state.market.filter(s=>ok(s.price)); if(!rows.length)return;
    const active=[...rows].sort((a,b)=>(b.volume||0)-(a.volume||0))[0];
    const unusual=[...rows].sort((a,b)=>(b.rel_volume||0)-(a.rel_volume||0))[0];
    const mover=[...rows].sort((a,b)=>Math.abs(b.change||0)-Math.abs(a.change||0))[0];
    const buys=rows.reduce((n,s)=>n+(s.insider_buys||0),0);
    $('#marketMetrics').innerHTML=`<article class="metric-card"><span>Nejvyšší objem</span><strong>${esc(active.ticker)}</strong><small>${compact(active.volume)} akcií</small></article><article class="metric-card"><span>Neobvyklý objem</span><strong>${esc(unusual.ticker)}</strong><small>${rel(unusual.rel_volume)} běžného objemu</small></article><article class="metric-card"><span>Největší pohyb</span><strong>${esc(mover.ticker)}</strong><small class="${cls(mover.change)}">${pct(mover.change)}</small></article><article class="metric-card"><span>Insider nákupy</span><strong>${buys}</strong><small>u sledovaných titulů</small></article>`;
  }
  function filteredStocks(){
    const q=$('#stockSearch').value.trim().toLowerCase(), f=$('#stockFilter').value, sort=$('#stockSort').value;
    let rows=state.market.filter(s=>!q||`${s.ticker} ${s.name}`.toLowerCase().includes(q));
    if(f==='unusual')rows=rows.filter(s=>(s.rel_volume||0)>=1.1);if(f==='gainers')rows=rows.filter(s=>(s.change||0)>0);if(f==='losers')rows=rows.filter(s=>(s.change||0)<0);if(f==='watch')rows=rows.filter(s=>state.watch.includes(s.ticker));
    const sorts={volume:(a,b)=>(b.volume||0)-(a.volume||0),relative:(a,b)=>(b.rel_volume||0)-(a.rel_volume||0),change:(a,b)=>(b.change||0)-(a.change||0),score:(a,b)=>scoreStock(b)-scoreStock(a)};
    return rows.sort(sorts[sort]||sorts.volume);
  }
  function renderStocks(){
    const rows=filteredStocks(), max=Math.max(1,...rows.map(s=>s.volume||0)); $('#stockEmpty').hidden=!!rows.length;
    $('#stockRows').innerHTML=rows.map(s=>{const score=scoreStock(s), watched=state.watch.includes(s.ticker);return `<tr><td><div class="stock-id"><span class="mini-logo">${esc(s.ticker.slice(0,2))}</span><button data-open="${esc(s.ticker)}"><b>${esc(s.ticker)}</b><small>${esc(s.name||'')}</small></button></div></td><td class="num"><b>${money(s.price)}</b></td><td class="num ${cls(s.change)}"><b>${pct(s.change)}</b></td><td class="num"><div class="volume-bar">${compact(s.volume)}<i style="--w:${Math.max(4,(s.volume||0)/max*100)}%"></i></div></td><td class="num ${Number(s.rel_volume)>=1.1?'up':''}">${rel(s.rel_volume)}</td><td class="num">${money(s.day_low)}–${money(s.day_high)}</td><td class="num"><span class="signal ${signalClass(score)}">${score}/100</span></td><td><button class="star-btn ${watched?'on':''}" data-watch="${esc(s.ticker)}" aria-label="${watched?'Odebrat':'Sledovat'} ${esc(s.ticker)}">${watched?'★':'☆'}</button></td></tr>`;}).join('');
  }
  ['stockSearch','stockFilter','stockSort'].forEach(id=>$('#'+id).addEventListener(id==='stockSearch'?'input':'change',renderStocks));
  function renderPulse(){const wanted=['SPY','QQQ','DIA','IWM'];const rows=wanted.map(t=>state.byTicker.get(t)).filter(Boolean);$('#marketPulse').innerHTML=rows.length?rows.map(s=>`<div class="pulse-row"><b>${s.ticker}</b><span>${money(s.price)}</span><span class="${cls(s.change)}">${pct(s.change)}</span></div>`).join(''):'<div class="empty-state">Indexy se připravují.</div>';}

  async function renderHighlights(){
    try{
      const [buys,gurus]=await Promise.all([data('feed-buys.json'),data('gurus.json')]);state.insiderData.buys=buys||[];state.gurus=gurus||[];
      const top=[...(buys||[])].sort((a,b)=>(b.sc||0)-(a.sc||0)||((b.s||0)*(b.p||0))-((a.s||0)*(a.p||0))).slice(0,5);
      $('#topInsiders').innerHTML=top.length?top.map(r=>`<div class="compact-item"><span class="badge buy">${esc(r.t||'—')}</span><div><b>${esc(title(r.o||'Neznámý insider'))}</b><small>${esc(r.r||r.i||'')} · obchod ${date(r.d)}</small></div><b>${compact((r.s||0)*(r.p||0))} $</b></div>`).join(''):'<div class="empty-state">Žádné nové nákupy.</div>';
      const moves=[];for(const g of gurus||[])for(const p of (g.pos||[]))if(['new','add','cut','out'].includes(p.ch))moves.push({g,p});moves.sort((a,b)=>(b.p.v||0)-(a.p.v||0));
      $('#topGurus').innerHTML=moves.length?moves.slice(0,5).map(({g,p})=>`<div class="compact-item"><span class="badge ${['new','add'].includes(p.ch)?'buy':'sell'}">${esc(p.t||'—')}</span><div><b>${esc(g.label||g.fund||'Investor')}</b><small>${esc(changeLabel(p.ch))} · stav ${date(g.period)}</small></div><b>${compact(p.v)} $</b></div>`).join(''):'<div class="empty-state">Data 13F se připravují.</div>';
    }catch{$('#topInsiders').innerHTML=$('#topGurus').innerHTML='<div class="empty-state">Data se nepodařilo načíst.</div>';}
  }

  async function openDetail(ticker){
    ticker=ticker.toUpperCase();state.selected=ticker;setView('detail');history.replaceState(null,'',`#${ticker}`);const s=state.byTicker.get(ticker)||{ticker,name:ticker};
    $('#detailTicker').textContent=ticker;$('#detailLogo').textContent=ticker.slice(0,2);$('#detailName').textContent=s.name||ticker;$('#detailPrice').textContent=money(s.price);$('#detailChange').textContent=pct(s.change);$('#detailChange').className=cls(s.change);$('#detailTime').textContent=`Tržní snapshot ${dateTime(state.marketMeta.updated)}`;renderDetailWatch();
    const yearPos=ok(s.year_high)&&ok(s.year_low)&&s.year_high!==s.year_low?((s.price-s.year_low)/(s.year_high-s.year_low)*100):null;
    $('#detailStats').innerHTML=`<div class="panel-head"><div><p class="eyebrow">KLÍČOVÉ HODNOTY</p><h2>${esc(ticker)}</h2></div></div><div class="stat-row"><span>Denní minimum</span><b>${money(s.day_low)}</b></div><div class="stat-row"><span>Denní maximum</span><b>${money(s.day_high)}</b></div><div class="stat-row"><span>Objem</span><b>${compact(s.volume)}</b></div><div class="stat-row"><span>20denní průměr</span><b>${compact(s.avg_volume)}</b></div><div class="stat-row"><span>Relativní objem</span><b>${rel(s.rel_volume)}</b></div><div class="stat-row"><span>52týdenní minimum</span><b>${money(s.year_low)}</b></div><div class="stat-row"><span>52týdenní maximum</span><b>${money(s.year_high)}</b></div><div class="stat-row"><span>Pozice v 52týdenním pásmu</span><b>${ok(yearPos)?num(yearPos,0)+' %':'—'}</b></div><div class="stat-row"><span>MarketTrace signál</span><b>${scoreStock(s)}/100</b></div>`;
    $('#priceChart').innerHTML='<div class="skeleton chart-skeleton"></div>';$('#detailInsiders').innerHTML='<div class="skeleton h40"></div>';renderTradingChart(ticker);
    try{const [bars,sec]=await Promise.all([data(`bars/${ticker}.json`),data(`t/${ticker}.json`).catch(()=>null)]);state.detailBars=bars||[];renderDetailInsiders(sec?.tx||[]);}catch{state.detailBars=[];renderDetailInsiders([]);}
  }
  function renderDetailWatch(){const b=$('#detailWatch'),on=state.watch.includes(state.selected);b.textContent=on?'★':'☆';b.classList.toggle('on',on);b.setAttribute('aria-label',`${on?'Odebrat':'Přidat'} ${state.selected||''}`);}
  $('#detailWatch').addEventListener('click',()=>state.selected&&toggleWatch(state.selected));
  let tvPromise;
  function loadTradingView(){
    if(window.TradingView)return Promise.resolve();
    if(tvPromise)return tvPromise;
    tvPromise=new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='https://s3.tradingview.com/tv.js';s.async=true;s.onload=resolve;s.onerror=()=>{tvPromise=null;reject(new Error('TradingView se nepodařilo načíst'));};document.head.appendChild(s);});
    return tvPromise;
  }
  function tradingViewSymbol(ticker){
    const arca=new Set(['SPY','IWM','DIA']),nyse=new Set(['JPM','V','LLY','UNH','WMT','XOM','CRM','UBER','DIS','BA','ABNB']);
    if(arca.has(ticker))return `AMEX:${ticker}`;if(ticker==='QQQ')return 'NASDAQ:QQQ';return `${nyse.has(ticker)?'NYSE':'NASDAQ'}:${ticker}`;
  }
  async function renderTradingChart(ticker){
    const box=$('#priceChart'),token=`tv_${Date.now()}`;box.innerHTML=`<div id="${token}"><div class="skeleton chart-skeleton"></div></div>`;
    $('#chartTitle').textContent=`${ticker} · detailní vývoj ceny`;
    try{
      await loadTradingView();if(state.selected!==ticker)return;
      box.innerHTML=`<div id="${token}"></div>`;
      new window.TradingView.widget({autosize:true,symbol:tradingViewSymbol(ticker),interval:state.chartInterval,timezone:'Europe/Prague',theme:document.documentElement.classList.contains('light')?'light':'dark',style:'1',locale:'cs',toolbar_bg:document.documentElement.classList.contains('light')?'#ffffff':'#0e1c2f',enable_publishing:false,allow_symbol_change:false,save_image:false,calendar:false,hide_side_toolbar:false,hide_top_toolbar:false,withdateranges:true,details:true,hotlist:false,studies:['Volume@tv-basicstudies'],container_id:token});
    }catch{if(state.selected===ticker)renderChart(state.detailBars);}
  }
  function renderChart(rows){
    const data=(rows||[]).slice(-252).filter(r=>ok(r[4]));if(data.length<2){$('#priceChart').innerHTML='<div class="empty-state">Cenová historie zatím není dostupná.</div>';return;}
    const vals=data.map(r=>+r[4]),min=Math.min(...vals),max=Math.max(...vals),range=max-min||1,w=900,h=300,p=20;const pts=vals.map((v,i)=>`${p+i/(vals.length-1)*(w-p*2)},${p+(max-v)/range*(h-p*2)}`).join(' ');const up=vals.at(-1)>=vals[0],color=up?'var(--green)':'var(--red)';
    const grid=[0,.25,.5,.75,1].map(x=>`<line x1="${p}" y1="${p+x*(h-p*2)}" x2="${w-p}" y2="${p+x*(h-p*2)}" stroke="var(--line)" stroke-width="1"/><text x="${w-p}" y="${p+x*(h-p*2)-5}" text-anchor="end" fill="var(--muted)" font-size="11">${money(max-x*range)}</text>`).join('');
    $('#priceChart').innerHTML=`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Cenový graf"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>${grid}<polygon points="${p},${h-p} ${pts} ${w-p},${h-p}" fill="url(#fill)"/><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="3" vector-effect="non-scaling-stroke"/></svg>`;
  }
  $('#chartIntervals').addEventListener('click',e=>{const b=e.target.closest('[data-interval]');if(!b||!state.selected)return;state.chartInterval=b.dataset.interval;$$('#chartIntervals button').forEach(x=>x.classList.toggle('active',x===b));renderTradingChart(state.selected);});
  $('#expandChart').addEventListener('click',()=>{const panel=$('#chartPanel'),on=!panel.classList.contains('fullscreen');panel.classList.toggle('fullscreen',on);$('#expandChart').setAttribute('aria-expanded',String(on));$('#expandChart').textContent=on?'✕ Zavřít celý graf':'⛶ Celá obrazovka';document.body.style.overflow=on?'hidden':'';setTimeout(()=>window.dispatchEvent(new Event('resize')),60);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#chartPanel').classList.contains('fullscreen'))$('#expandChart').click();});
  $('#chartResizer').addEventListener('pointerdown',e=>{e.preventDefault();const panel=$('#chartPanel'),start=e.clientY,startHeight=$('#priceChart').getBoundingClientRect().height;document.body.classList.add('chart-dragging');const move=ev=>{const h=Math.max(300,Math.min(900,startHeight+ev.clientY-start));panel.style.setProperty('--chart-height',`${h}px`);};const up=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',up);document.body.classList.remove('chart-dragging');STORE.set('mt_chart_height',Math.round($('#priceChart').getBoundingClientRect().height));window.dispatchEvent(new Event('resize'));};document.addEventListener('pointermove',move);document.addEventListener('pointerup',up);});
  const savedChartHeight=Number(STORE.get('mt_chart_height',520));if(savedChartHeight>=300&&savedChartHeight<=900)$('#chartPanel').style.setProperty('--chart-height',`${savedChartHeight}px`);
  function groupTrades(rows){const m=new Map();for(const r of rows){const k=`${r.a||''}|${r.k||''}|${r.o||''}`;const g=m.get(k)||{...r,value:0,shares:0};g.value+=(r.s||0)*(r.p||0);g.shares+=r.s||0;m.set(k,g);}return [...m.values()];}
  function renderDetailInsiders(rows){const list=groupTrades(rows).filter(r=>['P','S'].includes(r.k)).slice(0,9);$('#detailInsiders').innerHTML=list.length?list.map(tradeCard).join(''):'<div class="empty-state">Pro tento titul nejsou v uloženém období dostupné insider obchody.</div>';}
  function tradeCard(r){const buy=r.k==='P'||r.ad==='A',value=r.value||((r.s||0)*(r.p||0));return `<article class="data-card"><div class="card-top"><span class="badge ${buy?'buy':'sell'}">${buy?'Nákup':'Prodej'}</span><span>${esc(r.t||'')}</span><strong>${compact(value)} $</strong></div><h3>${esc(title(r.o||'Neznámý insider'))}</h3><p>${esc(r.r||r.i||'')} · obchod ${date(r.d)} · zveřejněno ${date(r.f)}</p><div class="card-tags">${r.pl?'<span class="badge">Plán 10b5-1</span>':''}${r.sc?`<span class="badge">Skóre ${esc(r.sc)}/10</span>`:''}</div>${r.u?`<a href="${esc(r.u)}" target="_blank" rel="noopener noreferrer">Originální hlášení SEC →</a>`:''}</article>`;}

  const INS_HINT={buys:'Nákupy na volném trhu, za které insider zaplatil vlastními penězi. Často mají vyšší vypovídací hodnotu než prodeje.',sells:'Velké prodeje mohou souviset s daněmi, diverzifikací nebo předem nastaveným plánem. Samy o sobě nejsou automaticky negativním signálem.',f144:'Form 144 oznamuje záměr prodat akcie ještě před uskutečněním prodeje.',f13d:'Schedule 13D zveřejňuje podíl nad 5 %, pokud investor může chtít firmu aktivně ovlivňovat.'};
  async function loadInsiders(){const mode=state.insiderMode;$('#insiderExplain').textContent=INS_HINT[mode];const box=$('#insiderFeed');box.innerHTML='<div class="skeleton h40"></div>';try{const file={buys:'feed-buys.json',sells:'feed-sells.json',f144:'f144.json',f13d:'f13d.json'}[mode];const rows=await data(file);state.insiderData[mode]=rows||[];box.innerHTML=(rows||[]).length?(mode==='f144'?(rows||[]).map(form144Card).join(''):mode==='f13d'?(rows||[]).map(form13dCard).join(''):groupTrades(rows||[]).slice(0,120).map(tradeCard).join('')):'<div class="empty-state">Žádná aktuální hlášení.</div>';}catch{box.innerHTML='<div class="empty-state">Data se nepodařilo načíst.</div>';}}
  $('#insiderFilters').addEventListener('click',e=>{const b=e.target.closest('[data-mode]');if(!b)return;state.insiderMode=b.dataset.mode;$$('#insiderFilters button').forEach(x=>x.classList.toggle('active',x===b));loadInsiders();});
  function form144Card(r){return `<article class="data-card"><div class="card-top"><span class="badge sell">Plánovaný prodej</span><span>${esc(r.t||'')}</span><strong>${compact(r.v)} $</strong></div><h3>${esc(title(r.o||'Insider'))}</h3><p>${esc(r.r||r.i||'')} · plán kolem ${date(r.d)} · podáno ${date(r.f)}</p><div class="card-tags">${r.pl?`<span class="badge">10b5-1 od ${date(r.pl)}</span>`:'<span class="badge sell">Bez uvedeného plánu</span>'}</div>${r.u?`<a href="${esc(r.u)}" target="_blank" rel="noopener noreferrer">Originální hlášení SEC →</a>`:''}</article>`;}
  function form13dCard(r){return `<article class="data-card"><div class="card-top"><span class="badge buy">${r.am?'Změna podílu':'Nový podíl'}</span><span>${esc(r.t||'')}</span><strong>${ok(r.pct)?num(r.pct,1)+' %':''}</strong></div><h3>${esc(title(r.o||'Investor'))}</h3><p>${esc(r.i||'')} · podáno ${date(r.f)}</p>${r.pu?`<p style="margin-top:10px">${esc(r.pu)}</p>`:''}${r.u?`<a href="${esc(r.u)}" target="_blank" rel="noopener noreferrer">Originální hlášení SEC →</a>`:''}</article>`;}

  async function loadGurus(){const box=$('#guruList');box.innerHTML='<div class="skeleton h40"></div>';try{const gurus=state.gurus.length?state.gurus:await data('gurus.json');state.gurus=gurus||[];box.innerHTML=state.gurus.length?state.gurus.map(guruCard).join(''):'<div class="empty-state">Data 13F se připravují.</div>';}catch{box.innerHTML='<div class="empty-state">Data se nepodařilo načíst.</div>';}}
  function guruCard(g){const pos=[...(g.pos||[])].filter(p=>p.ch!=='out').sort((a,b)=>(b.v||0)-(a.v||0)).slice(0,8),max=Math.max(1,...pos.map(p=>p.v||0));return `<article class="panel guru-card"><h2>${esc(g.label||g.fund||'Investor')}</h2><p>${esc(g.fund||'')} · stav ${date(g.period)} · ${pos.length} hlavních pozic</p><div class="guru-holdings">${pos.map(p=>`<div class="holding-row"><button class="link-btn" data-open="${esc(p.t||'')}">${esc(p.t||'—')}</button><div class="holding-bar"><i style="--w:${(p.v||0)/max*100}%"></i></div><b>${compact(p.v)} $</b></div>`).join('')}</div></article>`;}
  function changeLabel(ch){return {new:'Nová pozice',add:'Navýšení',cut:'Snížení',out:'Ukončená pozice'}[ch]||'Změna';}

  function renderPortfolio(){
    const groups=new Map();for(const l of state.portfolio){const g=groups.get(l.ticker)||{ticker:l.ticker,lots:[],qty:0,cost:0};g.lots.push(l);g.qty+=l.qty;g.cost+=l.qty*l.price;groups.set(l.ticker,g);}let value=0,cost=0,day=0,missing=0;
    for(const g of groups.values()){const s=state.byTicker.get(g.ticker);g.current=s?.price;g.value=ok(g.current)?g.qty*g.current:null;g.pl=ok(g.value)?g.value-g.cost:null;if(ok(g.value)){value+=g.value;cost+=g.cost;day+=g.qty*(g.current-(s.previous_close||g.current));}else missing++;}
    const pl=value-cost;$('#portfolioMetrics').innerHTML=`<article class="metric-card"><span>Hodnota portfolia</span><strong>${money(value)}</strong><small>${groups.size} titulů</small></article><article class="metric-card"><span>Zisk / ztráta</span><strong class="${cls(pl)}">${money(pl)}</strong><small class="${cls(pl)}">${pct(cost?pl/cost*100:null)}</small></article><article class="metric-card"><span>Dnes</span><strong class="${cls(day)}">${money(day)}</strong><small>podle posledního snapshotu</small></article><article class="metric-card"><span>Bez aktuální ceny</span><strong>${missing}</strong><small>pozic</small></article>`;
    const box=$('#portfolioList');box.innerHTML=groups.size?[...groups.values()].sort((a,b)=>(b.value||0)-(a.value||0)).map(g=>`<div class="portfolio-row"><div><button class="link-btn" data-open="${esc(g.ticker)}"><b>${esc(g.ticker)}</b></button><small>${num(g.qty,4)} ks · průměr ${money(g.cost/g.qty)}</small></div><div><small>Aktuální cena</small><b>${money(g.current)}</b></div><div><small>Hodnota</small><b>${money(g.value)}</b></div><div><small>Zisk / ztráta</small><b class="${cls(g.pl)}">${money(g.pl)}</b></div><div><small>Výnos</small><b class="${cls(g.pl)}">${pct(g.cost?g.pl/g.cost*100:null)}</b></div><button data-delete-group="${esc(g.ticker)}" aria-label="Smazat ${esc(g.ticker)}">×</button></div>`).join(''):'<div class="empty-state">Portfolio je zatím prázdné. Přidej první nákup vlevo.</div>';
  }
  $('#portfolioForm').addEventListener('submit',e=>{e.preventDefault();const ticker=$('#portfolioTicker').value.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,''),qty=parseFloat($('#portfolioQty').value),price=parseFloat($('#portfolioPrice').value);if(!ticker||!(qty>0)||!(price>0))return;state.portfolio.push({id:crypto.randomUUID?.()||String(Date.now()),ticker,qty,price,date:$('#portfolioDate').value||new Date().toISOString().slice(0,10)});STORE.set('mt_port_v2',state.portfolio);e.target.reset();$('#portfolioDate').value=new Date().toISOString().slice(0,10);renderPortfolio();toast(`${ticker} přidána do portfolia`);});
  $('#exportPortfolio').addEventListener('click',()=>{const blob=new Blob([JSON.stringify({version:1,exported:new Date().toISOString(),lots:state.portfolio},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`markettrace-portfolio-${new Date().toISOString().slice(0,10)}.json`;a.click();URL.revokeObjectURL(a.href);});
  $('#importPortfolio').addEventListener('change',async e=>{try{const j=JSON.parse(await e.target.files[0].text());if(!Array.isArray(j.lots))throw 0;state.portfolio=j.lots.filter(x=>x.ticker&&x.qty>0&&x.price>0);STORE.set('mt_port_v2',state.portfolio);renderPortfolio();toast('Portfolio bylo importováno');}catch{toast('Soubor není platná záloha MarketTrace');}e.target.value='';});

  document.addEventListener('click',e=>{const open=e.target.closest('[data-open]');if(open){e.preventDefault();openDetail(open.dataset.open);return;}const w=e.target.closest('[data-watch]');if(w){toggleWatch(w.dataset.watch);return;}const u=e.target.closest('[data-unwatch]');if(u){toggleWatch(u.dataset.unwatch);return;}const d=e.target.closest('[data-delete-group]');if(d){if(confirm(`Smazat všechny nákupy ${d.dataset.deleteGroup}?`)){state.portfolio=state.portfolio.filter(x=>x.ticker!==d.dataset.deleteGroup);STORE.set('mt_port_v2',state.portfolio);renderPortfolio();}}});
  $('#themeBtn').addEventListener('click',()=>{document.documentElement.classList.toggle('light');STORE.set('mt_theme',document.documentElement.classList.contains('light')?'light':'dark');if(state.view==='detail'&&state.selected)renderTradingChart(state.selected);});if(STORE.get('mt_theme','dark')==='light')document.documentElement.classList.add('light');
  function title(v){return String(v||'').toLowerCase().replace(/\b\p{L}/gu,m=>m.toUpperCase());}

  async function boot(){
    $('#portfolioDate').value=new Date().toISOString().slice(0,10);renderWatch();
    try{const m=await data('market.json',true);state.marketMeta=m;state.market=(m.symbols||[]).map(s=>({...s,score:scoreStock(s)}));state.byTicker=new Map(state.market.map(s=>[s.ticker,s]));const age=Date.now()-new Date(m.updated).getTime(),stale=age>45*60000;$('#feedStatus').className=`feed-status ${stale?'stale':'live'}`;$('#feedStatus b').textContent=stale?'Poslední dostupná data':'Data připojena';$('#updatedAt').textContent=`Poslední aktualizace ${dateTime(m.updated)} · zpoždění přibližně ${m.delay||15} min`;renderWatch();renderPulse();renderMetrics();renderStocks();renderHighlights();renderPortfolio();
    }catch(e){$('#feedStatus').className='feed-status error';$('#feedStatus b').textContent='Data nejsou dostupná';$('#stockRows').innerHTML='<tr><td colspan="8"><div class="empty-state">Tržní snapshot se právě připravuje. Zkus stránku obnovit za několik minut.</div></td></tr>';console.error(e);}
    const h=decodeURIComponent(location.hash.slice(1));if(/^[A-Z][A-Z0-9.\-]{0,9}$/.test(h.toUpperCase()))openDetail(h.toUpperCase());else if(['insiders','gurus','portfolio'].includes(h))setView(h);
  }
  boot();
})();
