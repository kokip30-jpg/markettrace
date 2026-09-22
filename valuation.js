/* MarketTrace – férová hodnota akcie a výhled podle cílových cen analytiků.
   Samostatný modul: sleduje otevřený detail tituly (#detailTicker) a vykreslí vlastní panel. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ok = v => v != null && v !== '' && !isNaN(v) && isFinite(v);
  const nfc = {};
  const num = (v, d = 2) => ok(v) ? (nfc[d] ||= new Intl.NumberFormat('cs-CZ', {minimumFractionDigits: d, maximumFractionDigits: d})).format(v) : '—';
  const usd = v => ok(v) ? num(v, Math.abs(v) >= 100 ? 0 : 2) + ' $' : '—';
  const pct = v => ok(v) ? (v > 0 ? '+' : '') + num(v, 0) + ' %' : '—';
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const bucket = () => Math.floor(Date.now() / 300000);
  const cache = {};
  async function data(name){
    const k = name + bucket();
    if(k in cache) return cache[k];
    try{
      const r = await fetch(`data/${name}?v=${bucket()}`, {cache: 'no-cache'});
      return cache[k] = r.ok ? await r.json() : null;
    }catch{ return cache[k] = null; }
  }

  /* ---------- vzhled ---------- */
  const css = document.createElement('style');
  css.textContent = `
  .fv-body{padding:16px 20px 20px}
  @media (max-width:760px){.fv-body{padding:14px 15px 16px}}
  .fv-wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:18px;margin:18px 0}
  @media (max-width:980px){.fv-wrap{grid-template-columns:minmax(0,1fr)}}
  .fv-verdict{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 4px}
  .fv-badge{font-weight:800;font-size:14px;padding:6px 12px;border-radius:999px}
  .fv-badge.under{color:var(--green);background:color-mix(in srgb,var(--green) 15%,transparent)}
  .fv-badge.fair{color:var(--blue);background:color-mix(in srgb,var(--blue) 15%,transparent)}
  .fv-badge.over{color:var(--red);background:color-mix(in srgb,var(--red) 15%,transparent)}
  .fv-badge.na{color:var(--muted);background:var(--panel2)}
  .fv-big{font-size:30px;font-weight:800;letter-spacing:-.03em;line-height:1.1}
  .fv-big small{display:block;font-size:14px;font-weight:600;color:var(--muted);margin-top:4px;letter-spacing:0}
  .fv-scale{position:relative;height:44px;margin:18px 4px 6px}
  .fv-track{position:absolute;left:0;right:0;top:18px;height:8px;border-radius:99px;background:linear-gradient(90deg,var(--green),var(--blue) 50%,var(--red))}
  .fv-mark{position:absolute;top:0;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;font-size:11px;font-weight:700;white-space:nowrap}
  .fv-mark i{width:2px;height:30px;background:var(--text);border-radius:2px;margin-top:2px}
  .fv-mark.fvm i{background:var(--muted)}
  .fv-mark span{position:absolute;top:34px}
  .fv-ends{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin:10px 0 0}
  .fv-methods{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}
  .fv-methods li{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:10px 12px;border:1px solid var(--line);border-radius:11px;background:var(--panel2)}
  .fv-methods b{font-size:14px}
  .fv-methods strong{text-align:right}
  .fv-methods small{grid-column:1/-1;color:var(--muted);font-size:12px;line-height:1.4}
  .fv-methods li.off{opacity:.6}
  .fv-note{color:var(--muted);font-size:12px;margin:12px 0 0;line-height:1.45}
  .tg-svg{width:100%;height:auto;display:block;margin-top:8px}
  .tg-svg text{fill:var(--muted);font-size:11px;font-family:inherit}
  .tg-svg .lbl{font-weight:800;font-size:12px}
  .tg-legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;margin-top:8px}
  .tg-legend span b{font-size:15px}
  .tg-rec{display:flex;height:10px;border-radius:99px;overflow:hidden;margin:14px 0 6px;background:var(--panel2)}
  .tg-rec i{display:block}
  .tg-recl{display:flex;gap:14px;font-size:12px;color:var(--muted);flex-wrap:wrap}
  `;
  document.head.appendChild(css);

  /* ---------- výpočet férové hodnoty ---------- */
  function growth(fund){
    const a = fund?.series?.revenue?.annual || [];
    if(a.length >= 2){
      const x = a[a.length - 1].value, y = a[a.length - 2].value;
      if(ok(x) && ok(y) && y > 0) return x / y - 1;
    }
    const q = fund?.series?.revenue?.quarterly || [];
    if(q.length >= 8){
      const s1 = q.slice(-4).reduce((s, v) => s + v.value, 0), s0 = q.slice(-8, -4).reduce((s, v) => s + v.value, 0);
      if(s0 > 0) return s1 / s0 - 1;
    }
    return null;
  }
  function dcf(fcfps, g, netCashPs){
    const r = 0.09, tg = 0.03;
    let v = 0, f = fcfps;
    for(let y = 1; y <= 10; y++){
      const gy = y <= 5 ? g : g - (g - tg) * (y - 5) / 5;
      f *= 1 + gy;
      v += f / Math.pow(1 + r, y);
    }
    v += f * (1 + tg) / (r - tg) / Math.pow(1 + r, 10);
    return v + (netCashPs || 0);
  }
  function evaluate(price, fund, tgt){
    const m = [];
    const t = fund?.ttm || {}, v = fund?.valuation || {};
    const shares = fund?.instant?.shares?.value;
    const g = growth(fund);
    const gUse = ok(g) ? clamp(g, -0.1, 0.3) : 0.05;
    // 1) analytici
    if(tgt && ok(tgt.avg)){
      const n = (tgt.buy || 0) + (tgt.hold || 0) + (tgt.sell || 0);
      m.push({key: 'an', name: 'Cílová cena analytiků', value: tgt.avg,
        why: `Průměr ${n ? n + ' analytiků' : 'analytiků'} na 12 měsíců dopředu (rozpětí ${usd(tgt.low)} až ${usd(tgt.high)}). Bývá spíš optimistická.`});
    }
    // 2) zisk × férové P/E podle růstu
    const eps = t.eps_diluted;
    if(ok(eps) && eps > 0){
      const gp = clamp((ok(g) ? g : 0.05) * 100, 0, 40);
      const fairPE = clamp(12 + 0.7 * gp, 12, 40);
      m.push({key: 'pe', name: 'Zisk a růst (P/E)', value: eps * fairPE,
        why: `Zisk na akcii ${num(eps)} $ × férové P/E ${num(fairPE, 0)} odvozené od růstu tržeb ${ok(g) ? pct(g * 100) : 'neznámého'} ročně. Dnešní P/E ${ok(price / eps) ? num(price / eps, 0) : '—'}.`});
    }else{
      m.push({key: 'pe', off: true, name: 'Zisk a růst (P/E)', value: null, why: 'Firma je za poslední rok ve ztrátě, ocenění podle zisku nedává smysl.'});
    }
    // 3) volné cash flow (DCF)
    const fcf = t.free_cash_flow;
    if(ok(fcf) && fcf > 0 && ok(shares) && shares > 0){
      const netCashPs = ok(v.net_debt) ? -v.net_debt / shares : 0;
      m.push({key: 'dcf', name: 'Volné cash flow (DCF)', value: dcf(fcf / shares, gUse, netCashPs),
        why: `Hotovost, kterou firma vydělá: dnes ${num(fcf / shares)} $ na akcii, růst ${pct(gUse * 100)} ročně s postupným zpomalením na 3 %, diskont 9 %.`});
    }else{
      m.push({key: 'dcf', off: true, name: 'Volné cash flow (DCF)', value: null, why: 'Firma zatím nevytváří kladné volné cash flow, výpočet nejde použít.'});
    }
    const vals = m.filter(x => !x.off && ok(x.value) && x.value > 0).map(x => x.value).sort((a, b) => a - b);
    if(!vals.length) return {methods: m, fair: null};
    const fair = vals.length === 3 ? vals[1] : vals.reduce((a, b) => a + b, 0) / vals.length;
    const diff = (fair / price - 1) * 100;
    const spread = vals[vals.length - 1] / vals[0];
    const verdict = diff > 15 ? 'under' : diff < -15 ? 'over' : 'fair';
    return {methods: m, fair, diff, verdict, lo: vals[0], hi: vals[vals.length - 1], unsure: vals.length === 1 || spread > 2, onlyAnalysts: vals.length === 1 && m[0].key === 'an' && !m[0].off};
  }

  /* ---------- panel: férová hodnota ---------- */
  function fairHtml(sym, price, ev){
    const label = {under: 'Podhodnocená', fair: 'Férově oceněná', over: 'Nadhodnocená'};
    if(!ev.fair){
      return `<div class="panel-head"><div><p class="eyebrow">FÉROVÁ HODNOTA</p><h2>Je ${esc(sym)} levná, nebo drahá?</h2></div></div><div class="fv-body">
        <div class="fv-verdict"><span class="fv-badge na">Nelze určit</span></div>
        <p class="fv-note">Firma nemá kladný zisk ani cash flow a analytici k ní nevydávají cílovou cenu, takže férovou hodnotu nejde rozumně spočítat.</p></div>`;
    }
    const lo = Math.min(ev.lo, price) * 0.85, hi = Math.max(ev.hi, price) * 1.1;
    const pos = v => clamp((v - lo) / (hi - lo) * 100, 2, 98);
    const methods = ev.methods.map(x => `<li class="${x.off ? 'off' : ''}"><b>${esc(x.name)}</b><strong>${x.off ? '—' : usd(x.value)}</strong><small>${esc(x.why)}</small></li>`).join('');
    return `<div class="panel-head"><div><p class="eyebrow">FÉROVÁ HODNOTA</p><h2>Je ${esc(sym)} levná, nebo drahá?</h2></div></div><div class="fv-body">
      <div class="fv-verdict"><span class="fv-badge ${ev.verdict}">${label[ev.verdict]}</span>${ev.unsure ? '<span class="fv-badge na">odhad je nejistý</span>' : ''}</div>
      <div class="fv-big">${usd(ev.fair)}<small>férová hodnota · dnes ${usd(price)} (${ev.diff > 0 ? 'o ' + num(ev.diff, 0) + ' % levnější' : 'o ' + num(-ev.diff, 0) + ' % dražší'})</small></div>
      <div class="fv-scale" aria-hidden="true"><div class="fv-track"></div>
        <div class="fv-mark fvm" style="left:${pos(ev.fair)}%">Férová<i></i></div>
        <div class="fv-mark" style="left:${pos(price)}%"><i></i><span>Dnes</span></div>
      </div>
      <div class="fv-ends"><span>levné</span><span>drahé</span></div>
      <ul class="fv-methods">${methods}</ul>
      <p class="fv-note">${ev.onlyAnalysts ? 'Firma je ve ztrátě, verdikt proto stojí jen na cílových cenách analytiků. ' : ''}Férová hodnota je medián použitých metod. Hranice pro verdikt je ±15 %. Jde o zjednodušený odhad, ne o investiční doporučení.</p></div>`;
  }

  /* ---------- panel: výhled analytiků ---------- */
  function targetHtml(sym, price, bars, tgt){
    const head = `<div class="panel-head"><div><p class="eyebrow">VÝHLED ANALYTIKŮ · 12 MĚSÍCŮ</p><h2>Kam podle analytiků ${esc(sym)} míří</h2></div><span class="source-badge">Nasdaq</span></div>`;
    if(!tgt || !ok(tgt.avg)) return head + '<div class="fv-body"><div class="empty-state">K tomuto titulu nejsou dostupné cílové ceny analytiků.</div></div>';
    const hist = (bars || []).slice(-252).map(b => ({d: b[0], c: b[4]})).filter(b => ok(b.c));
    if(!hist.length) hist.push({d: new Date().toISOString().slice(0, 10), c: price});
    const box = $('#fvTarget');
    const W = Math.max(300, Math.min(900, (box ? box.clientWidth : 720) - 34)), narrow = W < 520;
    const H = narrow ? 250 : 300, L = 4, R = narrow ? 104 : 150, T = 16, B = 26;
    const last = {d: hist[hist.length - 1].d, c: price || hist[hist.length - 1].c};
    const t0 = new Date(hist[0].d).getTime(), tNow = new Date(last.d).getTime(), tEnd = tNow + 365 * 864e5;
    const all = hist.map(h => h.c).concat([tgt.low, tgt.high, tgt.avg, last.c]).filter(ok);
    let yMin = Math.min(...all), yMax = Math.max(...all);
    const pad = (yMax - yMin) * 0.08 || yMax * 0.1; yMin -= pad; yMax += pad;
    const X = t => L + (t - t0) / (tEnd - t0) * (W - L - R);
    const Y = v => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);
    const path = hist.map((h, i) => `${i ? 'L' : 'M'}${X(new Date(h.d).getTime()).toFixed(1)},${Y(h.c).toFixed(1)}`).join('') + `L${X(tNow).toFixed(1)},${Y(last.c).toFixed(1)}`;
    const xn = X(tNow), yn = Y(last.c), xe = X(tEnd);
    const grid = [];
    for(let i = 0; i <= 4; i++){
      const v = yMin + (yMax - yMin) * i / 4, y = Y(v);
      grid.push(`<line x1="${L}" x2="${xe}" y1="${y}" y2="${y}" stroke="var(--line)" stroke-width="1"/>${i > 0 && i < 4 ? `<text x="${L + 2}" y="${y - 4}" opacity=".75">${num(v, v >= 100 ? 0 : 1)}</text>` : ''}`);
    }
    const dlab = t => new Date(t).toLocaleDateString('cs-CZ', {month: 'numeric', year: '2-digit'});
    const up = v => (v / last.c - 1) * 100;
    const ys = [[tgt.high, 'var(--green)', 'Max'], [tgt.avg, 'var(--blue)', 'Průměr'], [tgt.low, 'var(--red)', 'Min']];
    // štítky se nesmí překrývat: rozestup aspoň 30 px
    const ly = ys.map(([v]) => Y(v));
    for(let i = 1; i < ly.length; i++) if(ly[i] - ly[i - 1] < 30) ly[i] = ly[i - 1] + 30;
    const over = ly[ly.length - 1] - (H - B - 4);
    if(over > 0) for(let i = 0; i < ly.length; i++) ly[i] -= over;
    for(let i = ly.length - 2; i >= 0; i--) if(ly[i + 1] - ly[i] < 30) ly[i] = ly[i + 1] - 30;
    const line = ([v, color, name], i) => `<line x1="${xn}" y1="${yn}" x2="${xe}" y2="${Y(v)}" stroke="${color}" stroke-width="2.2" stroke-dasharray="6 5"/>
      <circle cx="${xe}" cy="${Y(v)}" r="4" fill="${color}"/>
      <text class="lbl" x="${xe + 8}" y="${ly[i] - 2}" style="fill:${color}">${name} ${usd(v)}</text>
      <text x="${xe + 8}" y="${ly[i] + 12}">${pct(up(v))}</text>`;
    const svg = `<svg class="tg-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Cena za rok a cílové ceny analytiků na 12 měsíců">
      ${grid.join('')}
      <polygon points="${xn},${yn} ${xe},${Y(tgt.high)} ${xe},${Y(tgt.low)}" fill="var(--blue)" opacity=".10"/>
      <path d="${path}" fill="none" stroke="var(--text)" stroke-width="2"/>
      <line x1="${xn}" x2="${xn}" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-dasharray="2 4"/>
      ${ys.map(line).join('')}
      <circle cx="${xn}" cy="${yn}" r="4.5" fill="var(--text)"/>
      <text x="${L}" y="${H - 8}">${dlab(t0)}</text>
      <text x="${xn}" y="${H - 8}" text-anchor="middle">dnes</text>
      <text x="${xe}" y="${H - 8}" text-anchor="end">${dlab(tEnd)}</text>
    </svg>`;
    const n = (tgt.buy || 0) + (tgt.hold || 0) + (tgt.sell || 0);
    const rec = n ? `<div class="tg-rec" aria-hidden="true">
        <i style="width:${tgt.buy / n * 100}%;background:var(--green)"></i><i style="width:${tgt.hold / n * 100}%;background:var(--amber)"></i><i style="width:${tgt.sell / n * 100}%;background:var(--red)"></i></div>
      <div class="tg-recl"><span>Koupit ${tgt.buy}</span><span>Držet ${tgt.hold}</span><span>Prodat ${tgt.sell}</span><span>celkem ${n} analytiků</span></div>` : '';
    return head + '<div class="fv-body">' + svg + `<div class="tg-legend">
        <span>Max <b style="color:var(--green)">${usd(tgt.high)}</b> ${pct(up(tgt.high))}</span>
        <span>Průměr <b style="color:var(--blue)">${usd(tgt.avg)}</b> ${pct(up(tgt.avg))}</span>
        <span>Min <b style="color:var(--red)">${usd(tgt.low)}</b> ${pct(up(tgt.low))}</span></div>${rec}
      <p class="fv-note">Čáry vedou od dnešní ceny k cílovým cenám analytiků za 12 měsíců. Nejde o předpověď vývoje po cestě, jen o to, kde analytici vidí cenu na konci období.</p></div>`;
  }

  /* ---------- napojení na detail ---------- */
  let current = null, req = 0;
  function ensureSection(){
    let sec = $('#fvSection');
    if(sec) return sec;
    const grid = $('#view-detail .detail-grid');
    if(!grid) return null;
    sec = document.createElement('section');
    sec.id = 'fvSection'; sec.className = 'fv-wrap';
    sec.innerHTML = '<article class="panel" id="fvFair"><div class="skeleton h40"></div></article><article class="panel" id="fvTarget"><div class="skeleton h40"></div></article>';
    grid.after(sec);
    return sec;
  }
  async function update(){
    const sym = ($('#detailTicker')?.textContent || '').trim().toUpperCase();
    if(!sym || sym === '—' || sym === current) return;
    current = sym;
    const my = ++req;
    if(!ensureSection()) return;
    $('#fvFair').innerHTML = $('#fvTarget').innerHTML = '<div class="skeleton h40"></div>';
    const [market, fund, tgt, bars] = await Promise.all([data('market.json'), data(`fundamentals/${sym}.json`), data(`targets/${sym}.json`), data(`bars/${sym}.json`)]);
    if(my !== req) return;
    const row = (market?.symbols || []).find(r => r.ticker === sym);
    const price = row?.price ?? (bars && bars.length ? bars[bars.length - 1][4] : null);
    if(!ok(price)){
      $('#fvFair').innerHTML = '<div class="empty-state">Pro tento titul zatím nemáme cenu.</div>';
      $('#fvTarget').innerHTML = '';
      return;
    }
    const t = tgt && !tgt.none ? tgt : null;
    $('#fvFair').innerHTML = fairHtml(sym, price, evaluate(price, fund, t));
    last = [sym, price, bars, t];
    $('#fvTarget').innerHTML = targetHtml(...last);
  }
  let last = null, rt = 0;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if(last && $('#fvTarget')) $('#fvTarget').innerHTML = targetHtml(...last); }, 200); });
  function start(){
    const el = $('#detailTicker');
    if(!el) return;
    new MutationObserver(update).observe(el, {childList: true, characterData: true, subtree: true});
    update();
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
