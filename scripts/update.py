#!/usr/bin/env python3
"""MarketTrace – stahuje data ze SEC EDGAR (Form 4, 13F) a ukládá je do složky data/.

Spouští se v GitHub Actions. Používá jen standardní knihovnu Pythonu.
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as fh:
    CFG = json.load(fh)
UA = CFG.get('user_agent') or 'MarketTrace markettrace@users.noreply.github.com'
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
START = time.time()
DEADLINE = START + CFG.get('max_minutes', 38) * 60


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def time_left():
    return time.time() < DEADLINE


# ---------------------------------------------------------------- HTTP
_last_sec = [0.0]
DEBUG = {'errors': [], 'probe': []}
UA_CANDIDATES = [UA, 'Pavel admin@masaze-tisnov.cz', 'MarketTrace admin@masaze-tisnov.cz']


def note_error(url, code, body=b''):
    try:
        body = gzip.decompress(body)
    except Exception:
        pass
    body = re.sub(rb'<[^>]*>|\s+', b' ', body or b'')
    if len(DEBUG['errors']) < 25:
        DEBUG['errors'].append({'url': url[:160], 'code': code,
                                'body': (body or b'')[:300].decode('utf-8', 'replace')})


def probe_sec():
    """Ověří, že SEC přijímá naše dotazy, a vybere funkční User-Agent."""
    global UA
    for ua in UA_CANDIDATES:
        try:
            req = urllib.request.Request('https://www.sec.gov/files/company_tickers.json',
                                         headers={'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate'})
            with urllib.request.urlopen(req, timeout=30) as r:
                DEBUG['probe'].append({'ua': ua, 'code': r.status})
                UA = ua
                return True
        except urllib.error.HTTPError as e:
            note_error('probe ' + ua, e.code, e.read())
            DEBUG['probe'].append({'ua': ua, 'code': e.code})
        except Exception as e:
            DEBUG['probe'].append({'ua': ua, 'error': repr(e)[:200]})
        time.sleep(2)
    return False


def http(url, data=None, headers=None, tries=4):
    h = {'User-Agent': UA, 'Accept-Encoding': 'gzip'}
    if headers:
        h.update(headers)
    is_sec = 'sec.gov' in url
    for i in range(tries):
        if is_sec:  # SEC povoluje max. 10 dotazů za sekundu
            wait = 0.15 - (time.time() - _last_sec[0])
            if wait > 0:
                time.sleep(wait)
            _last_sec[0] = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            try:
                note_error(url, e.code, e.read())
            except Exception:
                note_error(url, e.code)
            if e.code == 403 and i >= 1:
                return None
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep((10 if e.code in (403, 429) else 2) * (i + 1))
                continue
            log('HTTP', e.code, url)
            return None
        except Exception as e:  # síťové chyby, timeouty
            note_error(url, repr(e)[:120])
            log('síť', type(e).__name__, url)
            time.sleep(2 * (i + 1))
    log('vzdávám', url)
    return None


def get_json(url):
    b = http(url)
    if not b:
        return None
    try:
        return json.loads(b)
    except ValueError:
        return None


# ---------------------------------------------------------------- soubory
def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    p = os.path.join(DATA, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, p)


def clean(d):
    return {k: v for k, v in d.items() if v not in (None, False, [], '')}


# ---------------------------------------------------------------- XML
def strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return root


def xml_blocks(txt):
    return [m.group(1).strip() for m in re.finditer(r'<XML>(.*?)</XML>', txt, re.S | re.I)]


def parse_xml(s):
    s = re.sub(r'^\s*<\?xml[^>]*\?>', '', s.strip())
    return strip_ns(ET.fromstring(s))


def tx(el, path):
    if el is None:
        return None
    x = el.find(path)
    if x is None:
        return None
    t = ' '.join(''.join(x.itertext()).split())
    return t or None


def fnum(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(',', '').replace('$', ''))
    except ValueError:
        return None


def truthy(s):
    return (s or '').strip().lower() in ('1', 'true', 'y', 'yes')


def acc_folder(cik, acc):
    return f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}'


def txt_url(cik, acc):
    return f'{acc_folder(cik, acc)}/{acc}.txt'


def index_url(cik, acc):
    return f'{acc_folder(cik, acc)}/{acc}-index.htm'


# ---------------------------------------------------------------- Form 4
def parse_form4(txt, acc, filed, url):
    doc = None
    for b in xml_blocks(txt):
        if 'ownershipDocument' in b:
            try:
                doc = parse_xml(b)
                break
            except ET.ParseError:
                pass
    if doc is None:
        return []
    iss = doc.find('issuer')
    ticker = (tx(iss, 'issuerTradingSymbol') or '').upper()
    ticker = re.split(r'[\s,;/]+', ticker)[0] if ticker else ''
    if ticker in ('NONE', 'N/A', 'NA'):
        ticker = ''
    owners, role = [], None
    fl = {'dir': False, 'off': False, 'ten': False}
    for ro in doc.findall('reportingOwner'):
        nm = tx(ro, 'reportingOwnerId/rptOwnerName')
        if nm:
            owners.append(nm)
        rel = ro.find('reportingOwnerRelationship')
        if rel is None:
            continue
        if truthy(tx(rel, 'isDirector')):
            fl['dir'] = True
        if truthy(tx(rel, 'isOfficer')):
            fl['off'] = True
            role = role or tx(rel, 'officerTitle')
        if truthy(tx(rel, 'isTenPercentOwner')):
            fl['ten'] = True
        if truthy(tx(rel, 'isOther')) and not role:
            role = tx(rel, 'otherText')
    if not role:
        role = 'Director' if fl['dir'] else '10% owner' if fl['ten'] else 'Officer' if fl['off'] else None
    notes = {}
    fe = doc.find('footnotes')
    if fe is not None:
        for f in fe.findall('footnote'):
            notes[f.get('id')] = ' '.join(''.join(f.itertext()).split())
    doc_plan = truthy(tx(doc, 'aff10b5One'))
    out = []
    table = doc.find('nonDerivativeTable')
    if table is None:
        return out
    for t in table.findall('nonDerivativeTransaction'):
        ids = [x.get('id') for x in t.iter('footnoteId') if x.get('id')]
        fns = [notes[i] for i in dict.fromkeys(ids) if i in notes]
        out.append(clean({
            'a': acc, 'f': filed, 'u': url, 't': ticker,
            'i': tx(iss, 'issuerName'), 'c': tx(iss, 'issuerCik'),
            'o': ' / '.join(owners[:2]), 'r': role,
            'dr': fl['dir'], 'of': fl['off'], 'tp': fl['ten'],
            'd': tx(t, 'transactionDate/value'),
            'k': tx(t, 'transactionCoding/transactionCode'),
            'ad': tx(t, 'transactionAmounts/transactionAcquiredDisposedCode/value'),
            's': fnum(tx(t, 'transactionAmounts/transactionShares/value')),
            'p': fnum(tx(t, 'transactionAmounts/transactionPricePerShare/value')),
            'h': fnum(tx(t, 'postTransactionAmounts/sharesOwnedFollowingTransaction/value')),
            'n': tx(t, 'ownershipNature/directOrIndirectOwnership/value'),
            'nn': tx(t, 'ownershipNature/natureOfOwnership/value'),
            'pl': doc_plan or any('10b5-1' in n for n in fns),
            'fn': [n[:300] for n in fns][:4],
        }))
    return out


def fetch_form4(cik, acc, filed):
    b = http(txt_url(cik, acc))
    if not b:
        return None
    try:
        return parse_form4(b.decode('utf-8', 'replace'), acc, filed, index_url(cik, acc))
    except Exception as e:
        log('Form 4 nejde přečíst', acc, e)
        return []


# ---------------------------------------------------------------- celotržní přehled
def atom_feed(form, pages):
    out = []
    for start in range(0, pages * 100, 100):
        url = ('https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=' + form +
               f'&company=&dateb=&owner=include&start={start}&count=100&output=atom')
        b = http(url)
        if not b:
            break
        try:
            root = strip_ns(ET.fromstring(b))
        except ET.ParseError:
            break
        entries = root.findall('entry')
        for e in entries:
            m = re.search(r'accession-number=([\d-]+)', tx(e, 'id') or '')
            link = e.find('link')
            href = link.get('href') if link is not None else ''
            cat = e.find('category')
            if not m or cat is None or cat.get('term') != form:
                continue
            cm = re.search(r'/data/(\d+)/', href)
            if cm:
                out.append((m.group(1), cm.group(1), (tx(e, 'updated') or '')[:10]))
        if len(entries) < 100:
            break
    return out


def daily_index(day):
    q = (day.month - 1) // 3 + 1
    url = f'https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx'
    b = http(url)
    if not b:
        return []
    out = []
    for line in b.decode('latin-1').splitlines():
        if not line.startswith('4 '):
            continue
        m = re.search(r'\s(\d+)\s+(\d{8})\s+edgar/data/(\d+)/([\d-]+)\.txt\s*$', line)
        if m:
            d = m.group(2)
            out.append((m.group(4), m.group(3), f'{d[:4]}-{d[4:6]}-{d[6:]}'))
    return out


def update_feed(meta):
    seen = load('seen.json', {})
    buys = load('feed-buys.json', [])
    sells = load('feed-sells.json', [])
    todo, got = [], set()

    def add(items):
        for acc, cik, filed in items:
            if acc not in seen and acc not in got:
                got.add(acc)
                todo.append((acc, cik, filed))

    if not meta.get('backfilled'):
        day, n = TODAY, 0
        while n < CFG.get('backfill_days', 5):
            day -= timedelta(days=1)
            if day.weekday() < 5:
                add(daily_index(day))
                n += 1
        log('doplnění historie:', len(todo), 'hlášení')
    add(atom_feed('4', CFG.get('feed_pages', 4)))
    log('nová hlášení Form 4:', len(todo))

    min_sell = CFG.get('sell_min_value', 1_000_000)
    done = 0
    for acc, cik, filed in todo:
        if not time_left():
            log('došel čas, zbytek příště')
            break
        rows = fetch_form4(cik, acc, filed)
        if rows is None:
            continue
        seen[acc] = filed
        done += 1
        for r in rows:
            val = (r.get('s') or 0) * (r.get('p') or 0)
            if r.get('k') == 'P' and val > 0:
                buys.append(r)
            elif r.get('k') == 'S' and val >= min_sell:
                sells.append(r)
    if done and done == len(todo):
        meta['backfilled'] = True

    cut_b = (TODAY - timedelta(days=CFG.get('buy_days', 45))).isoformat()
    cut_s = (TODAY - timedelta(days=CFG.get('sell_days', 14))).isoformat()
    key = lambda r: (r.get('f', ''), r.get('a', ''))
    uniq = lambda rows: list({(r['a'], r.get('d'), r.get('s'), r.get('p'), r.get('o')): r for r in rows}.values())
    buys = sorted(uniq(r for r in buys if r.get('f', '') >= cut_b), key=key, reverse=True)
    sells = sorted(uniq(r for r in sells if r.get('f', '') >= cut_s), key=key, reverse=True)
    cut_seen = (TODAY - timedelta(days=40)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cut_seen}
    save('feed-buys.json', buys)
    save('feed-sells.json', sells)
    save('seen.json', seen)
    meta['feed'] = {'buys': len(buys), 'sells': len(sells), 'processed': done}
    log('přehled uložen:', len(buys), 'nákupů,', len(sells), 'prodejů')


# ---------------------------------------------------------------- sledované tickery
def ticker_map(meta):
    m = load('tickers.json', None)
    if m and meta.get('tickers_at', '') > (NOW - timedelta(days=7)).isoformat():
        return m
    j = get_json('https://www.sec.gov/files/company_tickers.json')
    if not j:
        return m or {}
    m = {}
    for v in j.values():
        t = v['ticker'].upper()
        if t not in m:
            m[t] = [v['cik_str'], v['title']]
    save('tickers.json', m)
    meta['tickers_at'] = NOW.isoformat()
    return m


def update_ticker(sym, tmap):
    if sym not in tmap:
        log('ticker není v SEC:', sym)
        return False
    cik, title = tmap[sym]
    sub = get_json(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json')
    if not sub:
        return False
    rec = sub['filings']['recent']
    since = (TODAY - timedelta(days=CFG.get('ticker_days', 365))).isoformat()
    f = load(f't/{sym}.json', {})
    known = f.get('acc', {})
    rows = f.get('tx', [])
    todo = [(a, d) for a, frm, d in zip(rec['accessionNumber'], rec['form'], rec['filingDate'])
            if frm == '4' and d >= since and a not in known]
    todo = todo[:CFG.get('ticker_max_new', 150)]
    for a, d in todo:
        if not time_left():
            break
        r = fetch_form4(cik, a, d)
        if r is None:
            continue
        known[a] = d
        rows.extend(r)
    rows = [r for r in rows if r.get('f', '') >= since]
    rows.sort(key=lambda r: (r.get('f', ''), r.get('d', '')), reverse=True)
    save(f't/{sym}.json', {
        't': sym, 'name': sub.get('name') or title, 'cik': cik,
        'acc': {k: v for k, v in known.items() if v >= since},
        'tx': rows, 'updated': NOW.isoformat(),
    })
    if todo:
        log(sym, '+', len(todo), 'hlášení')
    return True


# ---------------------------------------------------------------- guru (13F)
STOP = set('INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED PLC HLDGS HOLDINGS HOLDING GROUP GRP '
           'CL CLASS A B C COM NEW DEL THE SA NV AG LP LLC SHS ORD ADR SPONSORED SPON DE TR TRUST'.split())


def norm(s):
    s = re.sub(r'[^A-Z0-9 ]', ' ', (s or '').upper())
    return ' '.join(w for w in s.split() if w not in STOP)


def holdings(cik, acc):
    cache = load(f'13f/{acc}.json', None)
    if cache is not None:
        return cache
    b = http(txt_url(cik, acc))
    if not b:
        return None
    txt = b.decode('utf-8', 'replace')
    root = None
    for blk in xml_blocks(txt):
        if 'infoTable' in blk:
            try:
                root = parse_xml(blk)
                break
            except ET.ParseError:
                pass
    if root is None:
        return None
    agg = {}
    for it in root.iter('infoTable'):
        cu = (tx(it, 'cusip') or '').upper()
        pc = (tx(it, 'putCall') or '').upper()
        k = cu + ('|' + pc if pc else '')
        a = agg.setdefault(k, {'n': tx(it, 'nameOfIssuer'), 'cl': tx(it, 'titleOfClass'), 'cu': cu,
                               'pc': pc or None, 'v': 0.0, 's': 0.0})
        a['v'] += fnum(tx(it, 'value')) or 0
        a['s'] += fnum(tx(it, 'shrsOrPrnAmt/sshPrnamt')) or 0
    vals = sorted(a['v'] / a['s'] for a in agg.values() if a['s'] and a['v'])
    if vals and vals[len(vals) // 2] < 1:  # starší hlášení v tisících USD
        for a in agg.values():
            a['v'] *= 1000
    save(f'13f/{acc}.json', agg)
    return agg


def map_cusips(cusips, tmap, names):
    cache = load('cusip.json', {})
    todo = [c for c in dict.fromkeys(cusips) if c and c not in cache]
    for i in range(0, len(todo), 10):
        if not time_left():
            break
        batch = todo[i:i + 10]
        body = json.dumps([{'idType': 'ID_CUSIP', 'idValue': c} for c in batch]).encode()
        b = http('https://api.openfigi.com/v3/mapping', data=body, headers={'Content-Type': 'application/json'})
        if b is None:
            break
        try:
            res = json.loads(b)
        except ValueError:
            break
        for c, r in zip(batch, res):
            t = None
            data = r.get('data') or []
            for d in data:
                if d.get('exchCode') == 'US' and d.get('ticker'):
                    t = d['ticker']
                    break
            if not t and data:
                t = data[0].get('ticker')
            cache[c] = (t or '').replace('/', '.')
        time.sleep(2.6)  # OpenFIGI bez klíče: 25 dotazů za minutu
    by_name = {}
    for t, (_, title) in tmap.items():
        by_name.setdefault(norm(title), t)
    out = {}
    for c in cusips:
        out[c] = cache.get(c) or by_name.get(norm(names.get(c)), '')
    save('cusip.json', cache)
    return out


def update_gurus(tmap):
    result = []
    for g in CFG.get('gurus', []):
        if not time_left():
            break
        cik = int(g['cik'])
        sub = get_json(f'https://data.sec.gov/submissions/CIK{cik:010d}.json')
        if not sub:
            log('guru nenalezen', g)
            continue
        rec = sub['filings']['recent']
        per = {}
        for a, frm, d, p in zip(rec['accessionNumber'], rec['form'], rec['filingDate'], rec['reportDate']):
            if frm == '13F-HR' and p and p not in per:
                per[p] = (a, d)
        periods = sorted(per, reverse=True)[:2]
        if not periods:
            log('bez 13F', sub.get('name'))
            continue
        cur = holdings(cik, per[periods[0]][0])
        prev = holdings(cik, per[periods[1]][0]) if len(periods) > 1 else {}
        if not cur:
            continue
        prev = prev or {}
        total = sum(v['v'] for v in cur.values() if not v.get('pc'))
        pos = []
        for k in set(cur) | set(prev):
            c, p = cur.get(k), prev.get(k)
            base = c or p
            s, ps = (c or {}).get('s', 0), (p or {}).get('s', 0)
            if c and not p:
                ch = 'new'
            elif p and not c:
                ch = 'out'
            elif s > ps * 1.005:
                ch = 'add'
            elif s < ps * 0.995:
                ch = 'cut'
            else:
                ch = 'same'
            v = (c or {}).get('v', 0)
            pos.append(clean({
                'n': base['n'], 'cu': base['cu'], 'pc': base.get('pc'),
                'v': round(v), 's': s, 'pv': round((p or {}).get('v', 0)), 'ps': ps, 'ch': ch,
                'w': round(v / total * 100, 2) if total and not base.get('pc') else None,
            }))
        pos.sort(key=lambda x: (x.get('v', 0), x.get('pv', 0)), reverse=True)
        result.append({
            'cik': cik, 'label': g.get('label'), 'fund': sub.get('name'),
            'period': periods[0], 'filed': per[periods[0]][1],
            'prev': periods[1] if len(periods) > 1 else None,
            'total': round(total), 'count': sum(1 for x in pos if x['ch'] != 'out'), 'pos': pos,
        })
        log('guru', g.get('label'), periods[0], len(pos), 'pozic')
    cus = {p['cu']: p['n'] for g in result for p in g['pos']}
    tick = map_cusips(list(cus), tmap, cus)
    for g in result:
        for p in g['pos']:
            if tick.get(p['cu']):
                p['t'] = tick[p['cu']]
    return result


# ---------------------------------------------------------------- hlavní běh
def main():
    os.makedirs(DATA, exist_ok=True)
    meta = load('meta.json', {})
    if meta.get('feed', {}).get('buys', 0) == 0 and meta.get('feed', {}).get('sells', 0) == 0:
        meta.pop('backfilled', None)  # historie se zatím nestáhla
    if not probe_sec():
        log('SEC odmítá dotazy:', DEBUG['probe'])
        meta['error'] = 'SEC odmítá dotazy'
        meta['updated'] = datetime.now(timezone.utc).isoformat()
        save('meta.json', meta)
        save('debug.json', DEBUG)
        return
    meta.pop('error', None)
    tmap = ticker_map(meta)
    log('tickerů v SEC:', len(tmap))

    try:
        update_feed(meta)
    except Exception as e:
        log('CHYBA přehled:', repr(e))

    ok = []
    for sym in CFG.get('tickers', []):
        if not time_left():
            break
        try:
            if update_ticker(sym.upper(), tmap):
                ok.append(sym.upper())
        except Exception as e:
            log('CHYBA ticker', sym, repr(e))
    meta['tracked'] = sorted(set(meta.get('tracked', [])) | set(ok))

    stale = meta.get('gurus_at', '') < (NOW - timedelta(hours=CFG.get('guru_hours', 12))).isoformat()
    if stale and time_left():
        try:
            g = update_gurus(tmap)
            if g:
                save('gurus.json', g)
                meta['gurus_at'] = NOW.isoformat()
        except Exception as e:
            log('CHYBA guru:', repr(e))

    meta['updated'] = datetime.now(timezone.utc).isoformat()
    save('meta.json', meta)
    save('debug.json', DEBUG)
    log('hotovo za', round(time.time() - START), 's')


if __name__ == '__main__':
    sys.exit(main())
