/* MarketTrace – krytý call (samostatný modul, data z data/options.json) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const nf = {};
  const num = (v, d = 2) => v == null || isNaN(v) ? '—' : (nf[d] ||= new Intl.NumberFormat('cs-CZ', {minimumFractionDigits: d, maximumFractionDigits: d})).format(v);
  const pct = v => v == null || isNaN(v) ? '—' : (v > 0 ? '+' : '') + num(v) + ' %';
  const usd = v => v == null || isNaN(v) ? '—' : num(v) + ' $';
  const date = d => d ? new Date(d + 'T12:00:00').toLocaleDateString('cs-CZ') : '—';
  const ago = ms => { const m = Math.round((Date.now() - ms) / 60000); return m < 1 ? 'právě teď' : m < 60 ? `před ${m} min` : `před ${Math.floor(m / 60)} h`; };

  const RISK = {
    kons: {lo: .10, hi: .20, t: .15, label: 'Opatrně', note: 'odkup ~15 %'},
    vyv:  {lo: .20, hi: .32, t: .25, label: 'Vyváženě', note: 'odkup ~25 %'},
    agr:  {lo: .33, hi: .50, t: .40, label: 'Víc prémie', note: 'odkup ~40 %'}
  };
  const st = {risk: 'vyv', mine: false, noearn: false, data: null};

  const css = document.createElement('style');
  css.textContent = `
  .opt-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px 14px;margin:12px 0 4px}
  .opt-grid span{display:block;font-size:11px;color:var(--muted)}
  .opt-grid b{font-size:14px}
  .opt-strike{font-size:18px;font-weight:800;margin:8px 0 0}
  .opt-strike small{font-weight:500;color:var(--muted);font-size:13px}
  .opt-yield{color:var(--green)}
  .data-card details{margin-top:10px;font-size:13px}
  .data-card summary{cursor:pointer;color:var(--blue);font-weight:700}
  .opt-table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
  .opt-table th{color:var(--muted);font-weight:600;text-align:right;padding:5px 6px;border-bottom:1px solid var(--line)}
  .opt-table td{text-align:right;padding:5px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
  .opt-table th:first-child,.opt-table td:first-child{text-align:left}
  .opt-table tr.sel td{font-weight:800;color:var(--text)}
  .badge.warn{color:var(--amber);background:color-mix(in srgb,var(--amber) 14%,transparent)}
  .opt-open{border:0;background:none;padding:0;font-weight:800;font-size:16px;color:var(--text)}
  @media (max-width:560px){.opt-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}`;
  document.head.appendChild(css);

  function holdings(){
    try{
      const lots = JSON.parse(localStorage.getItem('mt_port_v2') || '[]');
      const h = {};
      for(const l of lots) h[l.ticker] = (h[l.ticker] || 0) + (+l.qty || 0);
      return h;
    }catch{ return {}; }
  }
  function pick(chain){
    const r = RISK[st.risk];
    const good = chain.filter(c => c.d >= r.lo && c.d <= r.hi && c.b >= 0.05 && (c.sp == null || c.sp <= 25));
    const pool = good.length ? good : chain.filter(c => c.b >= 0.05);
    return pool.length ? pool.slice().sort((a, b) => Math.abs(a.d - r.t) - Math.abs(b.d - r.t))[0] : null;
  }
  async function load(){
    const list = $('#optList');
    if(!st.data){
      list.innerHTML = '<div class="skeleton h40"></div>';
      try{
        const r = await fetch(`data/options.json?v=${Math.floor(Date.now() / 300000)}`, {cache: 'no-cache'});
        st.data = r.ok ? await r.json() : null;
      }catch{ st.data = null; }
      if(!st.data || !st.data.rows){ list.innerHTML = '<div class="empty-state">Opce se stahují, zkuste to za pár minut.</div>'; st.data = null; return; }
      $('#optUpdated').textContent = 'Aktualizováno ' + ago(new Date(st.data.updated).getTime());
      try{
        const ev = await fetch(`data/market.json?v=${Math.floor(Date.now() / 300000)}`).then(r => r.ok ? r.json() : null);
        st.names = {};
        for(const s of (ev && ev.symbols) || []) st.names[s.t || s.ticker || s.symbol] = s.n || s.name;
      }catch{}
    }
    render();
  }
  function render(){
    const h = holdings();
    let rows = st.data.rows.map(r => ({...r, p: pick(r.c)})).filter(r => r.p);
    if(st.mine) rows = rows.filter(r => h[r.t]);
    rows.sort((a, b) => b.p.ay - a.p.ay);
    $('#optCount').textContent = `${rows.length} titulů · seřazeno podle ročního výnosu z prémie`;
    if(!rows.length){
      $('#optList').innerHTML = `<div class="empty-state">${st.mine ? 'V portfoliu zatím nemáš žádný z tituly, pro které stahujeme opce. Přidej nákup v sekci Portfolio.' : 'Nic neodpovídá filtru.'}</div>`;
      return;
    }
    $('#optList').innerHTML = rows.map(r => {
      const c = r.p, qty = h[r.t] || 0, n = Math.floor(qty / 100);
      const tags = [];
      if(c.sp > 12) tags.push(`<span class="badge warn">Široký spread ${num(c.sp, 0)} %</span>`);
      if(qty && n < 1) tags.push('<span class="badge">Máš méně než 100 akcií</span>');
      if(n >= 1) tags.push(`<span class="badge buy">Můžeš prodat ${n} ${n === 1 ? 'kontrakt' : n < 5 ? 'kontrakty' : 'kontraktů'}</span>`);
      const rowsHtml = r.c.filter(x => x.b >= 0.05 && x.d <= 0.6).map(x => `<tr class="${x.k === c.k ? 'sel' : ''}">
          <td>${num(x.k)}</td><td>${num(x.b)}</td><td>${num(x.y)} % · ${num(x.ay, 0)} %</td><td>${pct(x.up)}</td><td>${num(x.d * 100, 0)} %</td></tr>`).join('');
      return `<article class="data-card">
        <div class="card-top"><button class="opt-open" data-ticker="${esc(r.t)}">${esc(r.t)}</button><span>cena ${usd(r.s)}</span><strong class="opt-yield">${num(c.ay, 1)} % ročně</strong></div>
        <p class="opt-strike">Strike ${num(c.k)} $ <small>· expirace ${date(r.exp)} (${r.dte} dní)</small></p>
        <div class="opt-grid">
          <div><span>Prémie za akcii</span><b>${usd(c.b)}</b></div>
          <div><span>Za 1 kontrakt</span><b>${num(c.b * 100, 0)} $</b></div>
          <div><span>Výnos za období</span><b>${num(c.y)} %</b></div>
          <div><span>Prostor ke strike</span><b>${pct(c.up)}</b></div>
          <div><span>Max. výnos při odkupu</span><b>${pct(c.mx)}</b></div>
          <div><span>Šance odkupu</span><b>${num(c.d * 100, 0)} %</b></div>
        </div>
        ${tags.length ? `<div class="card-tags">${tags.join('')}</div>` : ''}
        <details><summary>Ostatní striky</summary>
          <table class="opt-table"><thead><tr><th>Strike</th><th>Prémie</th><th>Výnos · ročně</th><th>Prostor</th><th>Odkup</th></tr></thead><tbody>${rowsHtml}</tbody></table>
          ${c.iv ? `<p style="color:var(--muted);margin:8px 0 0">Implikovaná volatilita ${num(c.iv, 0)} %. Nabídka ${num(c.b)} $, poptávka ${num(c.a)} $.</p>` : ''}
        </details>
      </article>`;
    }).join('');
  }

  function show(){
    if(window.MT){ window.MT.setView('options'); return; }
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === 'view-options'));
    document.querySelectorAll('.main-nav button').forEach(b => b.classList.toggle('active', b.dataset.view === 'options'));
    location.hash = 'options';
  }
  function build(){
    const nav = $('.main-nav');
    const main = $('.content');
    if(!nav || !main || $('#view-options')) return;
    const btn = document.createElement('button');
    btn.dataset.view = 'options'; btn.textContent = 'Opce';
    nav.appendChild(btn);
    const sec = document.createElement('section');
    sec.id = 'view-options'; sec.className = 'view';
    sec.innerHTML = `
      <div class="page-head"><div><p class="eyebrow">OPCE · KRYTÝ CALL</p><h1>Nejlepší strike na měsíc</h1>
        <p>Prodáš call na akcie, které držíš, a hned inkasuješ prémii. Když cena do expirace nepřekročí strike, prémie i akcie ti zůstanou. Když ho překročí, akcie prodáš za strike a růst nad ním ti uteče. 1 kontrakt = 100 akcií.</p></div>
        <div class="updated" id="optUpdated">—</div></div>
      <div class="filter-chips" id="optRisk">
        ${Object.entries(RISK).map(([k, r]) => `<button data-risk="${k}" class="${k === st.risk ? 'active' : ''}">${r.label} · ${r.note}</button>`).join('')}
        <button data-mine="1">Jen moje portfolio</button>
      </div>
      <div class="explain" id="optCount"></div>
      <div id="optList" class="cards-grid"></div>
      <div class="explain" style="margin-top:14px">Ceny opcí jsou orientační (bezplatný zdroj Alpaca), pokyn zadávej s limitní cenou. Šance odkupu je odhad podle delty. Před výsledky hospodaření bývá prémie vyšší, ale i riziko velkého pohybu. Nejde o investiční doporučení.</div>`;
    main.appendChild(sec);
    btn.addEventListener('click', () => { show(); load(); });
    sec.addEventListener('click', e => {
      const r = e.target.closest('[data-risk]');
      if(r){ st.risk = r.dataset.risk; sec.querySelectorAll('[data-risk]').forEach(b => b.classList.toggle('active', b === r)); if(st.data) render(); return; }
      const m = e.target.closest('[data-mine]');
      if(m){ st.mine = !st.mine; m.classList.toggle('active', st.mine); if(st.data) render(); return; }
      const t = e.target.closest('[data-ticker]');
      if(t && window.MT) window.MT.openDetail(t.dataset.ticker);
    });
    if(location.hash.slice(1).toLowerCase() === 'options'){ show(); load(); }
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();
