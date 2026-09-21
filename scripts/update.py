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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    score_buys(buys)
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


# ---------------------------------------------------------------- obecné hledání v XML
def find(root, name):
    if root is None:
        return None
    for el in root.iter():
        if el.tag == name:
            t = ' '.join(''.join(el.itertext()).split())
            if t:
                return t
    return None


def findall(root, name):
    if root is None:
        return []
    return [' '.join(''.join(el.itertext()).split()) for el in root.iter() if el.tag == name]


def find_like(root, sub):
    if root is None:
        return None
    sub = sub.lower()
    for el in root.iter():
        if isinstance(el.tag, str) and sub in el.tag.lower():
            t = ' '.join(''.join(el.itertext()).split())
            if t:
                return t
    return None


def us_date(s):
    if not s:
        return None
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else None


def first_xml(txt, *hints):
    for blk in xml_blocks(txt):
        if not hints or any(h in blk for h in hints):
            try:
                return parse_xml(blk)
            except ET.ParseError:
                pass
    return None


def atom_entries(typ, terms, pages):
    """Poslední hlášení daného typu z EDGAR, seskupená podle čísla hlášení."""
    out = {}
    for start in range(0, pages * 100, 100):
        url = ('https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=' + urllib.parse.quote(typ) +
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
            cat = e.find('category')
            term = cat.get('term') if cat is not None else ''
            if not m or term not in terms:
                continue
            link = e.find('link')
            href = link.get('href') if link is not None else ''
            d = out.setdefault(m.group(1), {'term': term, 'filed': (tx(e, 'updated') or '')[:10],
                                            'href': href, 'parties': []})
            cm = re.search(r'/data/(\d+)/', href)
            if cm and 'cik' not in d:
                d['cik'] = cm.group(1)
            t = re.match(r'^(.+?) - (.*) \((\d{10})\) \(([^)]+)\)\s*$', tx(e, 'title') or '')
            if t:
                d['parties'].append((t.group(2).strip(), t.group(3), t.group(4)))
        if len(entries) < 100:
            break
    return out


def party(d, *roles):
    for name, cik, role in d['parties']:
        if role.lower().startswith(roles):
            return name, cik
    return None, None


# ---------------------------------------------------------------- Form 144 (plánované prodeje)
def update_144(rev, seen):
    rows = load('f144.json', [])
    items = atom_entries('144', {'144', '144/A'}, CFG.get('f144_pages', 2))
    n = 0
    for acc, d in items.items():
        if acc in seen or 'cik' not in d:
            continue
        if not time_left():
            break
        b = http(txt_url(d['cik'], acc))
        if not b:
            continue
        seen[acc] = d['filed']
        root = first_xml(b.decode('utf-8', 'replace'), 'noOfUnitsSold', 'securitiesInformation', 'edgarSubmission')
        s_name, s_cik = party(d, 'subject', 'issuer')
        r_name, _ = party(d, 'reporting', 'filed', 'filer')
        icik = find(root, 'issuerCik') or s_cik
        val = sum(fnum(x) or 0 for x in findall(root, 'aggregateMarketValue'))
        sh = sum(fnum(x) or 0 for x in findall(root, 'noOfUnitsSold'))
        if not val or val < CFG.get('f144_min_value', 250_000):
            continue
        try:
            ticker = rev.get(str(int(icik)), '') if icik else ''
        except ValueError:
            ticker = ''
        rows.append(clean({
            'a': acc, 'f': d['filed'], 'u': index_url(d['cik'], acc), 't': ticker,
            'i': find(root, 'issuerName') or s_name, 'c': icik,
            'o': find(root, 'nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold') or r_name,
            'r': ', '.join(dict.fromkeys(findall(root, 'relationshipToIssuer'))) or None,
            's': sh, 'v': val, 'out': fnum(find(root, 'noOfUnitsOutstanding')),
            'd': us_date(find(root, 'approxSaleDate')),
            'na': find(root, 'natureOfAcquisitionTransaction'),
            'pl': us_date(find_like(root, 'planadoption')),
            'am': d['term'].endswith('/A'),
        }))
        n += 1
    cut = (TODAY - timedelta(days=CFG.get('f144_days', 14))).isoformat()
    rows = sorted(({r['a']: r for r in rows if r.get('f', '') >= cut}).values(),
                  key=lambda r: (r.get('f', ''), r.get('v', 0)), reverse=True)
    save('f144.json', rows)
    log('Form 144: +', n, ', celkem', len(rows))
    return rows


# ---------------------------------------------------------------- 13D (aktivisté)
def update_13d(rev, seen):
    rows = load('f13d.json', [])
    terms = {'SCHEDULE 13D', 'SCHEDULE 13D/A', 'SC 13D', 'SC 13D/A'}
    items = atom_entries('SCHEDULE 13D', terms, CFG.get('f13d_pages', 2))
    items.update({k: v for k, v in atom_entries('SC 13D', terms, 1).items() if k not in items})
    n = 0
    for acc, d in items.items():
        if acc in seen or 'cik' not in d:
            continue
        if not time_left():
            break
        b = http(txt_url(d['cik'], acc))
        if not b:
            continue
        seen[acc] = d['filed']
        root = first_xml(b.decode('utf-8', 'replace'), 'percentOfClass', 'edgarSubmission')
        s_name, s_cik = party(d, 'subject')
        filers = [nm for nm, _, role in d['parties'] if not role.lower().startswith('subject')]
        pcts = [fnum(x) for x in findall(root, 'percentOfClass')]
        amts = [fnum(x) for x in findall(root, 'aggregateAmountOwned')]
        pcts = [x for x in pcts if x is not None]
        amts = [x for x in amts if x is not None]
        icik = s_cik or find(root, 'issuerCik')
        try:
            ticker = rev.get(str(int(icik)), '') if icik else ''
        except ValueError:
            ticker = ''
        purpose = find_like(root, 'purpose')
        rows.append(clean({
            'a': acc, 'f': d['filed'], 'u': index_url(d['cik'], acc), 't': ticker,
            'i': s_name or find(root, 'issuerName'), 'c': icik,
            'o': ' / '.join(dict.fromkeys(filers)) or find(root, 'reportingPersonName'),
            'pct': max(pcts) if pcts else None, 's': max(amts) if amts else None,
            'ev': us_date(find_like(root, 'dateofevent')),
            'pu': purpose[:600] if purpose else None,
            'am': d['term'].endswith('/A'),
        }))
        n += 1
    cut = (TODAY - timedelta(days=CFG.get('f13d_days', 30))).isoformat()
    rows = sorted(({r['a']: r for r in rows if r.get('f', '') >= cut}).values(),
                  key=lambda r: r.get('f', ''), reverse=True)
    save('f13d.json', rows)
    log('13D: +', n, ', celkem', len(rows))
    return rows


# ---------------------------------------------------------------- Alpaca: grafy a ceny mimo hlavní seanci
NY = ZoneInfo('America/New_York')
AK, AS = os.environ.get('ALPACA_KEY', '').strip(), os.environ.get('ALPACA_SECRET', '').strip()


def alpaca_bars(symbols, timeframe, start, end):
    res, token = {}, None
    for _ in range(40):
        q = {'symbols': ','.join(symbols), 'timeframe': timeframe, 'start': start, 'end': end,
             'feed': 'sip', 'adjustment': 'split', 'limit': 10000}
        if token:
            q['page_token'] = token
        b = http('https://data.alpaca.markets/v2/stocks/bars?' + urllib.parse.urlencode(q),
                 headers={'APCA-API-KEY-ID': AK, 'APCA-API-SECRET-KEY': AS, 'Accept': 'application/json'})
        if not b:
            return None
        j = json.loads(b)
        for sym, bars in (j.get('bars') or {}).items():
            res.setdefault(sym, []).extend(bars)
        token = j.get('next_page_token')
        if not token:
            break
    return res


def session_of(ts):
    t = datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(NY)
    m = t.hour * 60 + t.minute
    return 'pre' if m < 570 else 'regular' if m < 960 else 'post'


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def update_alpaca(meta, symbols):
    if not (AK and AS):
        return
    now = datetime.now(timezone.utc)
    end = now - timedelta(minutes=16)  # zdarma jen se zpožděním 15 minut
    today_ny = now.astimezone(NY).date()
    if meta.get('bars_at', '')[:10] != today_ny.isoformat():
        daily = alpaca_bars(symbols, '1Day', iso(now - timedelta(days=400)), iso(end))
        if daily is None:
            log('Alpaca: denní svíčky se nepodařilo stáhnout')
        else:
            for sym, bars in daily.items():
                save(f'bars/{sym}.json', [[b['t'][:10], b['o'], b['h'], b['l'], b['c'], b['v']] for b in bars])
            meta['bars_at'] = today_ny.isoformat()
            log('Alpaca: denní svíčky pro', len(daily), 'titulů')
    ext = load('ext.json', {}).get('sym', {})
    ny = now.astimezone(NY)
    start = ny.replace(hour=4, minute=0, second=0, microsecond=0)
    if ny.weekday() < 5 and end > start.astimezone(timezone.utc):
        mins = alpaca_bars(symbols, '1Min', iso(start), iso(end))
        for sym, bars in (mins or {}).items():
            if not bars:
                continue
            tagged = [(b, session_of(b['t'])) for b in bars]
            last, sess = tagged[-1]
            same = [b for b, ss in tagged if ss == sess]
            daily = load(f'bars/{sym}.json', [])
            prev = [b for b in daily if b[0] < today_ny.isoformat()]
            reg = [b for b in daily if b[0] == today_ny.isoformat()]
            ext[sym] = clean({
                'p': last['c'], 't': last['t'], 's': sess,
                'pc': prev[-1][4] if prev else None,
                'rc': reg[-1][4] if reg and sess == 'post' else None,
                'h': max(b['h'] for b in same), 'l': min(b['l'] for b in same),
                'v': sum(b['v'] for b in same),
            })
        if mins:
            log('Alpaca: ceny mimo hlavní seanci pro', len(mins), 'titulů')
    save('ext.json', {'updated': iso(now), 'delay': 15, 'sym': ext})


# ---------------------------------------------------------------- kurz ČNB
def update_fx(meta):
    if meta.get('fx_at', '')[:10] == TODAY.isoformat():
        return
    b = http('https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/'
             'kurzy-devizoveho-trhu/denni_kurz.txt')
    if not b:
        return
    lines = b.decode('utf-8', 'replace').splitlines()
    fx = {'date': lines[0].split('#')[0].strip() if lines else ''}
    for ln in lines[2:]:
        parts = ln.split('|')
        if len(parts) == 5:
            try:
                fx[parts[3]] = float(parts[4].replace(',', '.')) / float(parts[2])
            except ValueError:
                pass
    if 'USD' in fx:
        save('fx.json', fx)
        meta['fx_at'] = NOW.isoformat()
        log('kurz ČNB USD', fx['USD'])


# ---------------------------------------------------------------- skóre nákupů
CEO_RE = re.compile(r'\b(CEO|Chief Executive|President|Chair|Chairman|Chairwoman|Founder)\b', re.I)
OFF_RE = re.compile(r'\b(CFO|COO|CTO|Chief|EVP|SVP|Executive Vice|General Counsel|Treasurer)\b', re.I)
FUND_RE = re.compile(r'\b(LLC|L\.?P\.?|FUND|CAPITAL|PARTNERS|HOLDINGS|TRUST|MANAGEMENT|INVESTMENTS?)\b', re.I)


def price_drop(sym, day):
    """Pokles ceny za 3 měsíce před nákupem v % (jen pro tituly s denními svíčkami)."""
    bars = load(f'bars/{sym}.json', None) if sym else None
    if not bars or not day:
        return None
    before = [b for b in bars if b[0] <= day]
    ago = (datetime.fromisoformat(day) - timedelta(days=91)).date().isoformat()
    old = [b for b in bars if b[0] <= ago]
    if not before or not old:
        return None
    return (before[-1][4] - old[-1][4]) / old[-1][4] * 100


def score_buys(rows):
    """Skóre 1–10 pro každý nákup (skupina řádků se stejným hlášením a osobou)."""
    deals = {}
    for r in rows:
        k = (r.get('a'), r.get('o'))
        g = deals.setdefault(k, {'rows': [], 's': 0.0, 'v': 0.0, 'h': None, 'd': '', 'r': r.get('r') or '',
                                 'c': r.get('c') or r.get('t'), 't': r.get('t'), 'o': r.get('o') or '',
                                 'of': r.get('of'), 'dr': r.get('dr'), 'tp': r.get('tp')})
        g['rows'].append(r)
        g['s'] += r.get('s') or 0
        g['v'] += (r.get('s') or 0) * (r.get('p') or 0)
        if (r.get('d') or '') >= g['d']:
            g['d'] = r.get('d') or g['d']
            g['h'] = r.get('h')
    by_issuer = {}
    for g in deals.values():
        by_issuer.setdefault(g['c'], []).append(g)
    for g in deals.values():
        why = []
        sc = 1.0
        role = g['r']
        if CEO_RE.search(role):
            sc += 3; why.append('CEO / předseda')
        elif OFF_RE.search(role) or g['of']:
            sc += 2; why.append('člen vedení')
        elif g['dr']:
            sc += 1; why.append('člen boardu')
        elif g['tp']:
            sc += 0.5
        v = g['v']
        if v >= 1e6:
            sc += 2; why.append('nad 1 mil. $')
        elif v >= 250e3:
            sc += 1.5
        elif v >= 100e3:
            sc += 1
        elif v >= 25e3:
            sc += 0.5
        elif v < 10e3:
            sc -= 1
        h, sh = g['h'], g['s']
        if h and sh:
            if h <= sh * 1.001:
                sc += 1.5; why.append('nová pozice')
            else:
                inc = sh / (h - sh) * 100
                if inc >= 50:
                    sc += 2; why.append(f'podíl +{inc:.0f} %')
                elif inc >= 20:
                    sc += 1.5; why.append(f'podíl +{inc:.0f} %')
                elif inc >= 10:
                    sc += 1
                elif inc >= 5:
                    sc += 0.5
        t0 = datetime.fromisoformat(g['d']) if g['d'] else None
        if t0:
            near = {x['o'] for x in by_issuer.get(g['c'], [])
                    if x['d'] and abs((datetime.fromisoformat(x['d']) - t0).days) <= 14}
            if len(near) >= 3:
                sc += 2; why.append(f'{len(near)} insideři najednou')
            elif len(near) == 2:
                sc += 1; why.append('2 insideři najednou')
        drop = price_drop(g['t'], g['d'])
        if drop is not None and drop <= -15:
            sc += 1; why.append(f'po poklesu {drop:.0f} %')
        avg = v / sh if sh else 0
        if 0 < avg < 1:
            sc -= 2; why.append('akcie pod 1 $')
        if g['tp'] and not g['of'] and not g['dr'] and FUND_RE.search(g['o']):
            sc -= 0.5
        final = max(1, min(10, round(sc)))
        for r in g['rows']:
            r['sc'] = final
            r['why'] = why[:4]
    return rows


# ---------------------------------------------------------------- upozornění (ntfy)
TOPIC = os.environ.get('NTFY_TOPIC', '').strip()
SITE = CFG.get('site_url', 'https://kokip30-jpg.github.io/markettrace/')


def ntfy(title, message, click=None, tags=None, priority=3):
    if not TOPIC:
        return False
    body = {'topic': TOPIC, 'title': title[:250], 'message': message[:3500], 'priority': priority}
    if click:
        body['click'] = click
    if tags:
        body['tags'] = tags
    b = http('https://ntfy.sh/', data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    return b is not None


def money_s(v):
    if v is None:
        return '—'
    for lim, suf in ((1e9, ' mld.'), (1e6, ' mil.'), (1e3, ' tis.')):
        if abs(v) >= lim:
            return f'${v / lim:.1f}'.replace('.', ',') + suf
    return f'${v:.0f}'


def notify(meta, f144, f13d, gurus_before, gurus_now):
    if not TOPIC:
        return
    ncfg = CFG.get('notify', {})
    watch = {t.upper() for t in ncfg.get('tickers', CFG.get('tickers', []))}
    min_score = ncfg.get('min_score', 8)
    sent = load('notified.json', {})
    first = not meta.get('notify_init')
    msgs = []

    buys = load('feed-buys.json', [])
    deals = {}
    for r in buys:
        deals.setdefault((r['a'], r.get('o')), []).append(r)
    clusters = {}
    for (acc, owner), rows in deals.items():
        r = rows[0]
        key = 'b:' + acc + ':' + (owner or '')
        v = sum((x.get('s') or 0) * (x.get('p') or 0) for x in rows)
        sc = r.get('sc', 0)
        if r.get('t') and (r.get('d') or '') >= (TODAY - timedelta(days=14)).isoformat():
            clusters.setdefault(r['t'], set()).add(owner)
        if key in sent or not (sc >= min_score or r.get('t') in watch):
            continue
        sent[key] = TODAY.isoformat()
        why = ', '.join(r.get('why') or [])
        msgs.append((sc, f"{r.get('t') or r.get('i')}: insider nakoupil za {money_s(v)} (skóre {sc}/10)",
                     f"{(owner or '').title()}, {r.get('r') or ''}\n{r.get('i') or ''}" + (f"\n{why}" if why else ''),
                     SITE + '#insideri', ['chart_with_upwards_trend']))
    for t, owners in clusters.items():
        key = f'c:{t}:{TODAY.isocalendar()[1]}'
        if len(owners) >= 3 and key not in sent and not any(k.startswith(f'c:{t}:') and v >= (TODAY - timedelta(days=14)).isoformat() for k, v in sent.items()):
            sent[key] = TODAY.isoformat()
            msgs.append((9, f'{t}: nakupuje {len(owners)} insiderů najednou', 'Skupinový nákup insiderů za posledních 14 dní.',
                         SITE + '#' + t, ['rotating_light']))
    for r in f144 or []:
        key = 'p:' + r['a']
        if key in sent or r.get('t') not in watch or (r.get('v') or 0) < ncfg.get('f144_min', 1e6):
            continue
        sent[key] = TODAY.isoformat()
        msgs.append((6, f"{r['t']}: plánovaný prodej za {money_s(r.get('v'))}",
                     f"{(r.get('o') or '').title()} ({r.get('r') or ''}) chce prodat {int(r.get('s') or 0):,} ks kolem {r.get('d') or '?'}.".replace(',', ' '),
                     SITE + '#insideri', ['warning']))
    for r in f13d or []:
        key = 'a:' + r['a']
        if key in sent or r.get('am') or not (r.get('t') in watch or ncfg.get('all_13d')):
            continue
        sent[key] = TODAY.isoformat()
        pct = f" {r['pct']:.1f} %".replace('.', ',') if r.get('pct') else ''
        msgs.append((7, f"{r.get('t') or r.get('i')}: aktivista získal{pct}", f"{r.get('o') or ''}", SITE + '#insideri', ['dart']))
    before = {g['cik']: g.get('period') for g in (gurus_before or [])}
    for g in gurus_now or []:
        key = f"g:{g['cik']}:{g.get('period')}"
        if key in sent or before.get(g['cik']) == g.get('period'):
            sent.setdefault(key, TODAY.isoformat())
            continue
        sent[key] = TODAY.isoformat()
        new = [p.get('t') or p.get('n') for p in g['pos'] if p.get('ch') == 'new' and not p.get('pc')][:5]
        out = [p.get('t') or p.get('n') for p in g['pos'] if p.get('ch') == 'out' and not p.get('pc')][:5]
        msgs.append((7, f"{g.get('label')}: nové portfolio ({g.get('period')})",
                     (f"Nové: {', '.join(new)}" if new else 'Bez nových pozic') + (f"\nProdal vše: {', '.join(out)}" if out else ''),
                     SITE + '#guru', ['moneybag']))

    if first:
        meta['notify_init'] = NOW.isoformat()
        ntfy('MarketTrace: upozornění jsou zapnutá', 'Od teď vám sem budou chodit silné nákupy insiderů, '
             'skupinové nákupy, plánované prodeje a změny v portfoliích guru investorů.', SITE, ['white_check_mark'])
    else:
        msgs.sort(key=lambda m: -m[0])
        limit = ncfg.get('max_per_run', 8)
        for sc, title, msg, click, tags in msgs[:limit]:
            ntfy(title, msg, click, tags, 4 if sc >= 9 else 3)
        if len(msgs) > limit:
            ntfy(f'MarketTrace: dalších {len(msgs) - limit} upozornění',
                 'Podrobnosti najdete v aplikaci.', SITE + '#insideri', ['bell'])
        if msgs:
            log('odesláno upozornění:', min(len(msgs), limit))
    cut = (TODAY - timedelta(days=60)).isoformat()
    save('notified.json', {k: v for k, v in sent.items() if v >= cut})


# ---------------------------------------------------------------- hlavní běh
def main():
    os.makedirs(DATA, exist_ok=True)
    meta = load('meta.json', {})
    tickers = [t.upper() for t in CFG.get('tickers', [])]

    for step in (lambda: update_alpaca(meta, tickers), lambda: update_fx(meta)):
        try:
            step()
        except Exception as e:
            log('CHYBA:', repr(e))

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
    rev = {}
    for t, (cik, _) in tmap.items():
        rev.setdefault(str(int(cik)), t)
    log('tickerů v SEC:', len(tmap))

    try:
        update_feed(meta)
    except Exception as e:
        log('CHYBA přehled:', repr(e))

    seen_x = load('seen-x.json', {})
    got = {}
    for fn in (update_144, update_13d):
        if time_left():
            try:
                got[fn.__name__] = fn(rev, seen_x)
            except Exception as e:
                log('CHYBA', fn.__name__, repr(e))
    cut = (TODAY - timedelta(days=40)).isoformat()
    save('seen-x.json', {k: v for k, v in seen_x.items() if v >= cut})

    ok = []
    for sym in tickers:
        if not time_left():
            break
        try:
            if update_ticker(sym, tmap):
                ok.append(sym)
        except Exception as e:
            log('CHYBA ticker', sym, repr(e))
    meta['tracked'] = sorted(set(ok) | (set(meta.get('tracked', [])) & set(tickers)))

    gurus_before = load('gurus.json', [])
    gurus_now = None
    stale = meta.get('gurus_at', '') < (NOW - timedelta(hours=CFG.get('guru_hours', 12))).isoformat()
    if stale and time_left():
        try:
            g = update_gurus(tmap)
            if g:
                gurus_now = g
                save('gurus.json', g)
                meta['gurus_at'] = NOW.isoformat()
        except Exception as e:
            log('CHYBA guru:', repr(e))

    try:
        notify(meta, got.get('update_144'), got.get('update_13d'), gurus_before, gurus_now)
    except Exception as e:
        log('CHYBA upozornění:', repr(e))
    meta['alpaca'] = bool(AK and AS)
    meta['notify'] = bool(TOPIC)
    meta['updated'] = datetime.now(timezone.utc).isoformat()
    save('meta.json', meta)
    save('debug.json', DEBUG)
    log('hotovo za', round(time.time() - START), 's')


if __name__ == '__main__':
    sys.exit(main())
