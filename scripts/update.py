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
MARKET_ONLY = '--market-only' in sys.argv


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def time_left():
    return time.time() < DEADLINE


# ---------------------------------------------------------------- HTTP
_last_sec = [0.0]
DEBUG = {'errors': [], 'probe': []}
UA_CANDIDATES = [UA, 'MarketTrace markettrace@users.noreply.github.com']


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
            # data.sec.gov bývá z GitHub runnerů dostupné i v době, kdy
            # www.sec.gov blokuje obecnou kontrolní adresu.
            req = urllib.request.Request('https://data.sec.gov/submissions/CIK0000320193.json',
                                         headers={'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate'})
            with urllib.request.urlopen(req, timeout=30) as r:
                DEBUG['probe'].append({'code': r.status})
                UA = ua
                return True
        except urllib.error.HTTPError as e:
            note_error('probe ' + ua, e.code, e.read())
            DEBUG['probe'].append({'code': e.code})
        except Exception as e:
            DEBUG['probe'].append({'error': type(e).__name__})
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


# ---------------------------------------------------------------- fundamenty + udalosti 8-K
DURATION_FACTS = {
    'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues',
                'SalesRevenueNet', 'SalesRevenueGoodsNet'),
    'net_income': ('NetIncomeLoss', 'ProfitLoss'),
    'operating_income': ('OperatingIncomeLoss',),
    'operating_cash': ('NetCashProvidedByUsedInOperatingActivities',
                       'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'),
    'capex': ('PaymentsToAcquirePropertyPlantAndEquipment',
              'PaymentsForAdditionsToPropertyPlantAndEquipment'),
    'eps_diluted': ('EarningsPerShareDiluted',),
}
INSTANT_FACTS = {
    'cash': ('CashAndCashEquivalentsAtCarryingValue',
             'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'),
    'assets': ('Assets',),
    'liabilities': ('Liabilities',),
    'equity': ('StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),
    'shares': ('EntityCommonStockSharesOutstanding', 'CommonStockSharesOutstanding'),
    'debt_current': ('LongTermDebtCurrent', 'ShortTermBorrowings'),
    'debt_long': ('LongTermDebtNoncurrent', 'LongTermDebt'),
}
EVENT_LABELS = {
    '1.01': 'Významná smlouva', '1.02': 'Ukončení významné smlouvy', '1.03': 'Úpadek nebo nucená správa',
    '2.01': 'Akvizice nebo prodej aktiv', '2.02': 'Výsledky a finanční situace',
    '2.03': 'Nový významný dluh', '2.04': 'Urychlení nebo zesplatnění závazku',
    '2.05': 'Náklady na restrukturalizaci', '2.06': 'Významné snížení hodnoty aktiv',
    '3.01': 'Oznámení burzy nebo vyřazení', '3.02': 'Neregistrovaný prodej akcií',
    '3.03': 'Změna práv držitelů akcií', '4.01': 'Změna auditora', '4.02': 'Účetní závěrka již není spolehlivá',
    '5.01': 'Změna kontroly společnosti', '5.02': 'Změna vedení nebo představenstva',
    '5.03': 'Změna stanov', '5.07': 'Výsledky hlasování akcionářů',
    '7.01': 'Regulation FD – nové veřejné sdělení', '8.01': 'Jiná významná událost',
    '9.01': 'Finanční výkazy a přílohy',
}


def fact_units(companyfacts, concepts):
    schemas = companyfacts.get('facts') or {}
    for schema in ('us-gaap', 'dei'):
        facts = schemas.get(schema) or {}
        for concept in concepts:
            units = (facts.get(concept) or {}).get('units') or {}
            for unit in ('USD', 'USD/shares', 'shares'):
                if units.get(unit):
                    return units[unit], concept, unit
    return [], None, None


def duration_series(companyfacts, concepts):
    rows, concept, unit = fact_units(companyfacts, concepts)
    picked = {}
    for r in rows:
        if r.get('form') not in ('10-Q', '10-K') or not r.get('start') or not r.get('end'):
            continue
        try:
            days = (datetime.fromisoformat(r['end']) - datetime.fromisoformat(r['start'])).days
        except ValueError:
            continue
        annual = r.get('form') == '10-K' and 300 <= days <= 390
        quarter = r.get('form') in ('10-Q', '10-K') and 70 <= days <= 120
        if not (annual or quarter):
            continue
        kind = 'annual' if annual else 'quarterly'
        key = (kind, r['end'])
        if key not in picked or (r.get('filed') or '') > (picked[key].get('filed') or ''):
            picked[key] = r
    out = {'annual': [], 'quarterly': [], 'concept': concept, 'unit': unit}
    for (kind, _), r in sorted(picked.items(), key=lambda x: x[0][1]):
        out[kind].append(clean({'start': r.get('start'), 'end': r.get('end'), 'filed': r.get('filed'), 'fy': r.get('fy'),
                                'fp': r.get('fp'), 'value': r.get('val')}))
    # Mnoho emitentů v XBRL neposílá samostatné Q4. Dopočítáme ho jako
    # celý fiskální rok minus první tři samostatná čtvrtletí.
    quarter_ends = {r.get('end') for r in out['quarterly']}
    for annual_row in out['annual']:
        start, end = annual_row.get('start'), annual_row.get('end')
        if not start or not end or end in quarter_ends or not isinstance(annual_row.get('value'), (int, float)):
            continue
        within = [r for r in out['quarterly'] if start <= (r.get('end') or '') < end and isinstance(r.get('value'), (int, float))]
        within = sorted(within, key=lambda r: r['end'])[-3:]
        if len(within) == 3:
            out['quarterly'].append({'start': within[-1]['end'], 'end': end, 'filed': annual_row.get('filed'),
                                      'fy': annual_row.get('fy'), 'fp': 'Q4',
                                      'value': annual_row['value'] - sum(r['value'] for r in within), 'derived': True})
            quarter_ends.add(end)
    out['quarterly'].sort(key=lambda r: r.get('end') or '')
    out['annual'] = out['annual'][-6:]
    out['quarterly'] = out['quarterly'][-8:]
    return out


def instant_latest(companyfacts, concepts):
    rows, concept, unit = fact_units(companyfacts, concepts)
    valid = [r for r in rows if r.get('form') in ('10-Q', '10-K', '10-K/A', '10-Q/A') and r.get('end')]
    if not valid:
        return None
    r = max(valid, key=lambda x: (x.get('end') or '', x.get('filed') or ''))
    return clean({'end': r.get('end'), 'filed': r.get('filed'), 'value': r.get('val'),
                  'concept': concept, 'unit': unit})


def last_value(series, kind='quarterly'):
    rows = (series or {}).get(kind) or []
    return rows[-1].get('value') if rows else None


def trailing(series):
    rows = (series or {}).get('quarterly') or []
    vals = [r.get('value') for r in rows[-4:] if isinstance(r.get('value'), (int, float))]
    return sum(vals) if len(vals) == 4 else None


def safe_div(a, b):
    return a / b if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b else None


def filing_events(sub, cik):
    rec = (sub.get('filings') or {}).get('recent') or {}
    keys = ('accessionNumber', 'form', 'filingDate', 'reportDate', 'primaryDocument', 'items')
    cols = {k: rec.get(k) or [] for k in keys}
    size = len(cols['accessionNumber'])
    events = []
    for i in range(size):
        if i >= len(cols['form']) or cols['form'][i] not in ('8-K', '8-K/A'):
            continue
        filed = cols['filingDate'][i] if i < len(cols['filingDate']) else None
        if filed and filed < (TODAY - timedelta(days=550)).isoformat():
            continue
        acc = cols['accessionNumber'][i]
        raw_items = cols['items'][i] if i < len(cols['items']) else ''
        items = re.findall(r'\d\.\d{2}', raw_items or '')
        labels = []
        for item in items:
            label = EVENT_LABELS.get(item)
            if label and label not in labels:
                labels.append(label)
        primary = cols['primaryDocument'][i] if i < len(cols['primaryDocument']) else ''
        url = f'{acc_folder(cik, acc)}/{primary}' if primary else index_url(cik, acc)
        events.append(clean({'filed': filed, 'report': cols['reportDate'][i] if i < len(cols['reportDate']) else None,
                             'form': cols['form'][i], 'items': items, 'labels': labels,
                             'title': labels[0] if labels else 'Významná firemní událost', 'url': url}))
        if len(events) >= 18:
            break
    return events


def update_fundamentals(sym, tmap, price=None):
    if sym not in tmap:
        return False
    cik, title = tmap[sym]
    sub = get_json(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json')
    facts = get_json(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json')
    if not sub or not facts:
        return False
    series = {name: duration_series(facts, concepts) for name, concepts in DURATION_FACTS.items()}
    instant = {name: instant_latest(facts, concepts) for name, concepts in INSTANT_FACTS.items()}
    revenue_ttm, income_ttm = trailing(series['revenue']), trailing(series['net_income'])
    op_income_ttm, cfo_ttm, capex_ttm = trailing(series['operating_income']), trailing(series['operating_cash']), trailing(series['capex'])
    eps_ttm = trailing(series['eps_diluted'])
    shares = (instant.get('shares') or {}).get('value')
    market_cap = price * shares if isinstance(price, (int, float)) and isinstance(shares, (int, float)) else None
    fcf_ttm = cfo_ttm - capex_ttm if isinstance(cfo_ttm, (int, float)) and isinstance(capex_ttm, (int, float)) else None
    debt = sum((instant.get(k) or {}).get('value') or 0 for k in ('debt_current', 'debt_long')) or None
    cash = (instant.get('cash') or {}).get('value')
    equity = (instant.get('equity') or {}).get('value')
    qrev = series['revenue']['quarterly']
    rev_growth = safe_div(qrev[-1]['value'], qrev[-5]['value']) - 1 if len(qrev) >= 5 and qrev[-5].get('value') else None
    valuation = clean({
        'price': price, 'market_cap': market_cap, 'pe_ttm': safe_div(price, eps_ttm),
        'ps_ttm': safe_div(market_cap, revenue_ttm), 'fcf_yield': safe_div(fcf_ttm, market_cap),
        'net_margin': safe_div(income_ttm, revenue_ttm), 'operating_margin': safe_div(op_income_ttm, revenue_ttm),
        'roe': safe_div(income_ttm, equity), 'revenue_growth_yoy': rev_growth,
        'net_debt': debt - cash if isinstance(debt, (int, float)) and isinstance(cash, (int, float)) else None,
    })
    save(f'fundamentals/{sym}.json', {
        'ticker': sym, 'name': sub.get('name') or title, 'cik': cik, 'updated': NOW.isoformat(),
        'series': series, 'instant': instant, 'ttm': clean({'revenue': revenue_ttm, 'net_income': income_ttm,
            'operating_income': op_income_ttm, 'operating_cash': cfo_ttm, 'capex': capex_ttm,
            'free_cash_flow': fcf_ttm, 'eps_diluted': eps_ttm}),
        'valuation': valuation, 'events': filing_events(sub, cik),
        'source': 'SEC EDGAR XBRL',
    })
    log('fundamenty', sym, 'událostí 8-K:', len(filing_events(sub, cik)))
    return True


def nasdaq_json(path):
    return get_json('https://api.nasdaq.com/api/' + path)


def nasdaq_number(value, thousands=False):
    s = str(value or '').strip().replace('$', '').replace(',', '').replace('%', '')
    if not s or s in ('--', 'N/A'):
        return None
    negative = s.startswith('(') and s.endswith(')')
    s = s.strip('()')
    mult = 1
    if s[-1:].upper() in ('K', 'M', 'B', 'T'):
        mult = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}[s[-1].upper()]
        s = s[:-1]
    try:
        n = float(s) * mult * (1000 if thousands and mult == 1 else 1)
        return -n if negative else n
    except ValueError:
        return None


def nasdaq_table(doc, table_name, thousands=True):
    table = ((doc or {}).get('data') or {}).get(table_name) or {}
    headers, rows = table.get('headers') or {}, table.get('rows') or []
    by_name = {str(r.get('value1') or '').strip().lower(): r for r in rows}
    periods = []
    for key in ('value2', 'value3', 'value4', 'value5'):
        raw_date = headers.get(key)
        if not raw_date:
            continue
        try:
            end = datetime.strptime(raw_date, '%m/%d/%Y').date().isoformat()
        except ValueError:
            end = raw_date
        periods.append((key, end))
    def values(*labels, absolute=False):
        row = next((by_name.get(label.lower()) for label in labels if by_name.get(label.lower())), None)
        out = []
        for key, end in periods:
            val = nasdaq_number((row or {}).get(key), thousands)
            if absolute and isinstance(val, (int, float)):
                val = abs(val)
            if val is not None:
                out.append({'end': end, 'value': val})
        return sorted(out, key=lambda r: r['end'])
    return values


def nasdaq_events(sym, headers):
    b = http(f'https://api.nasdaq.com/api/company/{urllib.parse.quote(sym)}/sec-filings?limit=40&sortColumn=filed&sortOrder=desc',
             headers=headers)
    try:
        data = (json.loads(b) if b else {}).get('data') or {}
    except ValueError:
        return []
    rows = data.get('rows') or (data.get('filings') or {}).get('rows') or []
    events = []
    for row in rows:
        form = str(row.get('formType') or row.get('form') or row.get('type') or '')
        if not form.upper().startswith('8-K'):
            continue
        description = str(row.get('description') or row.get('title') or '')
        low = description.lower()
        if any(k in low for k in ('result', 'financial', 'earnings')):
            title = 'Výsledky a finanční situace'
        elif any(k in low for k in ('director', 'officer', 'management')):
            title = 'Změna vedení nebo představenstva'
        elif any(k in low for k in ('acquisition', 'merger', 'asset')):
            title = 'Akvizice nebo prodej aktiv'
        elif any(k in low for k in ('agreement', 'contract')):
            title = 'Významná smlouva'
        else:
            title = 'Významná firemní událost'
        view = row.get('view') or row.get('url') or row.get('link')
        if isinstance(view, dict):
            view = view.get('html') or view.get('value') or next(iter(view.values()), None)
        events.append(clean({'filed': row.get('filed') or row.get('filingDate') or row.get('date'),
                             'form': form, 'title': title, 'labels': [description] if description else [],
                             'url': view}))
        if len(events) >= 18:
            break
    return events


def update_fundamentals_nasdaq(sym, price=None):
    headers = {'Accept': 'application/json, text/plain, */*', 'Origin': 'https://www.nasdaq.com',
               'Referer': f'https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/financials',
               'User-Agent': 'Mozilla/5.0 (compatible; MarketTrace/1.0)'}
    def fetch(frequency):
        b = http(f'https://api.nasdaq.com/api/company/{urllib.parse.quote(sym)}/financials?frequency={frequency}', headers=headers)
        try:
            return json.loads(b) if b else None
        except ValueError:
            return None
    annual_doc, quarterly_doc = fetch(1), fetch(2)
    if not annual_doc and not quarterly_doc:
        return False
    annual_income = nasdaq_table(annual_doc, 'incomeStatementTable')
    quarterly_income = nasdaq_table(quarterly_doc, 'incomeStatementTable')
    annual_cash = nasdaq_table(annual_doc, 'cashFlowTable')
    quarterly_cash = nasdaq_table(quarterly_doc, 'cashFlowTable')
    annual_balance = nasdaq_table(annual_doc, 'balanceSheetTable')
    quarterly_balance = nasdaq_table(quarterly_doc, 'balanceSheetTable')
    def series(a, q):
        return {'annual': a[-6:], 'quarterly': q[-8:]}
    revenue = series(annual_income('Total Revenue'), quarterly_income('Total Revenue'))
    net_income = series(annual_income('Net Income'), quarterly_income('Net Income'))
    operating_income = series(annual_income('Operating Income'), quarterly_income('Operating Income'))
    operating_cash = series(annual_cash('Net Cash Flow-Operating'), quarterly_cash('Net Cash Flow-Operating'))
    capex = series(annual_cash('Capital Expenditures', absolute=True), quarterly_cash('Capital Expenditures', absolute=True))
    def latest(values):
        rows = values or []
        return {'end': rows[-1]['end'], 'value': rows[-1]['value']} if rows else None
    cash_rows = quarterly_balance('Cash and Cash Equivalents') or annual_balance('Cash and Cash Equivalents')
    assets_rows = quarterly_balance('Total Assets') or annual_balance('Total Assets')
    liabilities_rows = quarterly_balance('Total Liabilities') or annual_balance('Total Liabilities')
    equity_rows = quarterly_balance('Total Equity') or annual_balance('Total Equity')
    short_rows = quarterly_balance('Short-Term Debt / Current Portion of Long-Term Debt') or annual_balance('Short-Term Debt / Current Portion of Long-Term Debt')
    long_rows = quarterly_balance('Long-Term Debt') or annual_balance('Long-Term Debt')
    summary_b = http(f'https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym)}/summary?assetclass=stocks', headers=headers)
    try:
        summary = ((json.loads(summary_b) if summary_b else {}).get('data') or {}).get('summaryData') or {}
    except ValueError:
        summary = {}
    market_cap = nasdaq_number((summary.get('MarketCap') or {}).get('value'))
    pe_ratio = nasdaq_number((summary.get('PERatio') or {}).get('value'))
    shares = market_cap / price if isinstance(market_cap, (int, float)) and isinstance(price, (int, float)) and price else None
    instant = {'cash': latest(cash_rows), 'assets': latest(assets_rows), 'liabilities': latest(liabilities_rows),
               'equity': latest(equity_rows), 'debt_current': latest(short_rows), 'debt_long': latest(long_rows)}
    if shares:
        instant['shares'] = {'end': TODAY.isoformat(), 'value': shares}
    def ttm_or_annual(s):
        vals = [r['value'] for r in s['quarterly'][-4:]]
        return sum(vals) if len(vals) == 4 else (s['annual'][-1]['value'] if s['annual'] else None)
    revenue_ttm, income_ttm = ttm_or_annual(revenue), ttm_or_annual(net_income)
    op_ttm, cfo_ttm, capex_ttm = ttm_or_annual(operating_income), ttm_or_annual(operating_cash), ttm_or_annual(capex)
    fcf_ttm = cfo_ttm - capex_ttm if isinstance(cfo_ttm, (int, float)) and isinstance(capex_ttm, (int, float)) else None
    cash = (instant.get('cash') or {}).get('value')
    debt = sum((instant.get(k) or {}).get('value') or 0 for k in ('debt_current', 'debt_long')) or None
    equity = (instant.get('equity') or {}).get('value')
    qrev = revenue['quarterly']
    growth = qrev[-1]['value'] / qrev[-5]['value'] - 1 if len(qrev) >= 5 and qrev[-5]['value'] else None
    eps_approx = income_ttm / shares if isinstance(income_ttm, (int, float)) and isinstance(shares, (int, float)) and shares else None
    pe_ratio = pe_ratio or safe_div(market_cap, income_ttm)
    save(f'fundamentals/{sym}.json', {
        'ticker': sym, 'name': sym, 'updated': NOW.isoformat(),
        'series': {'revenue': revenue, 'net_income': net_income, 'operating_income': operating_income,
                   'operating_cash': operating_cash, 'capex': capex, 'eps_diluted': {'annual': [], 'quarterly': []}},
        'instant': instant,
        'ttm': clean({'revenue': revenue_ttm, 'net_income': income_ttm, 'operating_income': op_ttm,
                      'operating_cash': cfo_ttm, 'capex': capex_ttm, 'free_cash_flow': fcf_ttm,
                      'eps_diluted': eps_approx}),
        'valuation': clean({'market_cap': market_cap, 'pe_ttm': pe_ratio,
                            'ps_ttm': safe_div(market_cap, revenue_ttm), 'fcf_yield': safe_div(fcf_ttm, market_cap),
                            'net_margin': safe_div(income_ttm, revenue_ttm),
                            'operating_margin': safe_div(op_ttm, revenue_ttm), 'roe': safe_div(income_ttm, equity),
                            'revenue_growth_yoy': growth,
                            'net_debt': debt - cash if isinstance(debt, (int, float)) and isinstance(cash, (int, float)) else None}),
        'events': nasdaq_events(sym, headers), 'source': 'Nasdaq / firemní výkazy',
    })
    log('fundamenty Nasdaq', sym)
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
    # Alpaca řadí vícesymbolové výsledky podle tickeru. Jeden velký dotaz může
    # vyčerpat stránkovací limit dřív, než se dostane k tickerům na konci abecedy.
    # Menší dávky zaručí kompletní historii pro celý screener.
    res = {}
    for offset in range(0, len(symbols), 5):
        batch, token = symbols[offset:offset + 5], None
        for _ in range(60):
            q = {'symbols': ','.join(batch), 'timeframe': timeframe, 'start': start, 'end': end,
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
    missing_daily = [sym for sym in symbols if not os.path.exists(os.path.join(DATA, 'bars', f'{sym}.json'))]
    refresh_daily = (meta.get('bars_at', '')[:10] != today_ny.isoformat()
                     or meta.get('bars_history_schema') != 4)
    daily_targets = symbols if refresh_daily else missing_daily
    if daily_targets:
        daily = alpaca_bars(daily_targets, '1Day', iso(now - timedelta(days=1100)), iso(end))
        if daily is None:
            log('Alpaca: denní svíčky se nepodařilo stáhnout')
        else:
            for sym, bars in daily.items():
                save(f'bars/{sym}.json', [[b['t'][:10], b['o'], b['h'], b['l'], b['c'], b['v']] for b in bars])
            meta['bars_at'] = today_ny.isoformat()
            meta['bars_history_schema'] = 4
            log('Alpaca: denní svíčky pro', len(daily), 'titulů')
    missing_hourly = [sym for sym in symbols if not os.path.exists(os.path.join(DATA, 'hourly', f'{sym}.json'))]
    refresh_hourly = (meta.get('hourly_at', '')[:10] != today_ny.isoformat()
                      or meta.get('hourly_schema') != 3)
    hourly_targets = symbols if refresh_hourly else missing_hourly
    if hourly_targets:
        hourly = alpaca_bars(hourly_targets, '1Hour', iso(now - timedelta(days=120)), iso(end))
        if hourly is None:
            log('Alpaca: hodinové svíčky se nepodařilo stáhnout')
        else:
            for sym, bars in hourly.items():
                save(f'hourly/{sym}.json', [[b['t'], b['o'], b['h'], b['l'], b['c'], b['v']]
                                             for b in bars][-1600:])
            meta['hourly_at'] = today_ny.isoformat()
            meta['hourly_schema'] = 3
            log('Alpaca: hodinové svíčky pro', len(hourly), 'titulů')
    ext = load('ext.json', {}).get('sym', {})
    ny = now.astimezone(NY)
    session_start = ny.replace(hour=4, minute=0, second=0, microsecond=0)
    # Nově přidaným titulům připravíme několik obchodních dnů historie,
    # zatímco existující tituly stahují jen dnešní přírůstek.
    missing_intraday = [sym for sym in symbols if not os.path.exists(os.path.join(DATA, 'intraday', f'{sym}.json'))]
    current_symbols = [sym for sym in symbols if sym not in missing_intraday]
    mins = {}
    if ny.weekday() < 5 and end > session_start.astimezone(timezone.utc) and current_symbols:
        mins.update(alpaca_bars(current_symbols, '1Min', iso(session_start), iso(end)) or {})
    if missing_intraday:
        backfill = alpaca_bars(missing_intraday, '1Min', iso(now - timedelta(days=8)), iso(end)) or {}
        for sym, rows in backfill.items():
            mins.setdefault(sym, []).extend(rows)
    if mins:
        for sym, bars in (mins or {}).items():
            if not bars:
                continue
            existing = load(f'intraday/{sym}.json', [])
            merged = {str(row[0]): row for row in existing
                      if isinstance(row, list) and len(row) >= 6}
            for b in bars:
                merged[b['t']] = [b['t'], b['o'], b['h'], b['l'], b['c'], b['v']]
            cutoff = now - timedelta(days=10)
            packed = [row for row in merged.values()
                      if datetime.fromisoformat(str(row[0]).replace('Z', '+00:00')) >= cutoff]
            packed.sort(key=lambda row: row[0])
            save(f'intraday/{sym}.json', packed[-5000:])

            today_bars = [b for b in bars
                          if datetime.fromisoformat(b['t'].replace('Z', '+00:00')).astimezone(NY).date() == today_ny]
            tagged = [(b, session_of(b['t'])) for b in today_bars]
            if not tagged:
                continue
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
        meta['intraday_at'] = iso(now)
        log('Alpaca: minutové grafy a ceny pro', len(mins), 'titulů')
    save('ext.json', {'updated': iso(now), 'delay': 15, 'sym': ext})


# ---------------------------------------------------------------- kryptoměny (Alpaca, bez zpoždění)
def crypto_bars(pairs, timeframe, start):
    res, token = {}, None
    for _ in range(60):
        q = {'symbols': ','.join(pairs), 'timeframe': timeframe, 'start': start, 'limit': 10000}
        if token:
            q['page_token'] = token
        b = http('https://data.alpaca.markets/v1beta3/crypto/us/bars?' + urllib.parse.urlencode(q),
                 headers={'APCA-API-KEY-ID': AK, 'APCA-API-SECRET-KEY': AS, 'Accept': 'application/json'})
        if not b:
            return res or None
        j = json.loads(b)
        for sym, bars in (j.get('bars') or {}).items():
            res.setdefault(sym, []).extend(bars)
        token = j.get('next_page_token')
        if not token:
            break
    return res


def update_crypto(meta, pairs):
    """Kryptoměny ukládá ve stejném tvaru jako akcie (bars/, intraday/, ext.json) pod tickerem BTC-USD."""
    if not (AK and AS) or not pairs:
        return []
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    sym = {p: p.replace('/', '-') for p in pairs}
    missing = [p for p in pairs if not os.path.exists(os.path.join(DATA, 'bars', f'{sym[p]}.json'))]
    refresh_daily = (meta.get('crypto_bars_at', '')[:10] != today
                     or meta.get('crypto_bars_history_schema') != 2)
    if refresh_daily or missing:
        daily = crypto_bars(pairs, '1Day', iso(now - timedelta(days=1100)))
        for p, bars in (daily or {}).items():
            if p in sym:
                save(f'bars/{sym[p]}.json', [[b['t'][:10], b['o'], b['h'], b['l'], b['c'], b['v']] for b in bars])
        if daily:
            meta['crypto_bars_at'] = today
            meta['crypto_bars_history_schema'] = 2
    missing_hourly = [p for p in pairs if not os.path.exists(os.path.join(DATA, 'hourly', f'{sym[p]}.json'))]
    refresh_hourly = (meta.get('crypto_hourly_at', '')[:10] != today
                      or meta.get('crypto_hourly_schema') != 1)
    if refresh_hourly or missing_hourly:
        hourly = crypto_bars(pairs, '1Hour', iso(now - timedelta(days=120)))
        for p, bars in (hourly or {}).items():
            if p in sym:
                save(f'hourly/{sym[p]}.json', [[b['t'], b['o'], b['h'], b['l'], b['c'], b['v']]
                                                for b in bars][-3000:])
        if hourly:
            meta['crypto_hourly_at'] = today
            meta['crypto_hourly_schema'] = 1
    lasts = []
    for p in pairs:
        rows = load(f'intraday/{sym[p]}.json', [])
        lasts.append(rows[-1][0] if rows else None)
    start = min([x for x in lasts if x] or [iso(now - timedelta(days=3))]) if all(lasts) else iso(now - timedelta(days=3))
    mins = crypto_bars(pairs, '1Min', start) or {}
    ext_doc = load('ext.json', {})
    ext = ext_doc.get('sym', {})
    done = []
    for p in pairs:
        s_ = sym[p]
        merged = {str(r[0]): r for r in load(f'intraday/{s_}.json', []) if isinstance(r, list) and len(r) >= 6}
        for b in mins.get(p, []):
            merged[b['t']] = [b['t'], b['o'], b['h'], b['l'], b['c'], b['v']]
        cutoff = now - timedelta(days=4)
        packed = sorted((r for r in merged.values()
                         if datetime.fromisoformat(str(r[0]).replace('Z', '+00:00')) >= cutoff), key=lambda r: r[0])[-5000:]
        if not packed:
            continue
        save(f'intraday/{s_}.json', packed)
        todays = [r for r in packed if r[0][:10] == today] or packed[-1:]
        daily = load(f'bars/{s_}.json', [])
        prev = [b for b in daily if b[0] < today]
        ext[s_] = clean({'p': packed[-1][4], 't': packed[-1][0], 's': 'regular',
                         'pc': prev[-1][4] if prev else None,
                         'h': max(r[2] for r in todays), 'l': min(r[3] for r in todays),
                         'v': sum(r[5] or 0 for r in todays)})
        done.append(s_)
    ext_doc['sym'] = ext
    save('ext.json', ext_doc)
    if done:
        log('kryptoměny:', ', '.join(done))
    return done


# ---------------------------------------------------------------- index volatility VIX (Cboe, zpoždění ~15 min)
def update_vix(meta, sym='VIX'):
    now = datetime.now(timezone.utc)
    hdr = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
           'Accept': 'application/json, text/csv, */*', 'Referer': 'https://www.cboe.com/'}
    if meta.get('vix_bars_at', '')[:10] != now.date().isoformat() or not os.path.exists(os.path.join(DATA, 'bars', f'{sym}.json')):
        b = http('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv', headers=hdr)
        rows = []
        for line in (b or b'').decode('utf-8', 'replace').splitlines()[1:]:
            parts = line.strip().split(',')
            if len(parts) < 5:
                continue
            m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', parts[0])
            d = f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}' if m else parts[0][:10]
            vals = [fnum(x) for x in parts[1:5]]
            if all(v is not None for v in vals):
                rows.append([d] + vals + [0])
        if rows:
            save(f'bars/{sym}.json', rows[-400:])
            meta['vix_bars_at'] = now.isoformat()
    ext_doc = load('ext.json', {})
    ext = ext_doc.get('sym', {})
    q = get_json_h('https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json', hdr)
    d = (q or {}).get('data') or {}
    price = fnum(d.get('current_price') or d.get('close'))
    if price:
        t = str(d.get('last_trade_time') or '')
        try:
            ts = iso(datetime.fromisoformat(t.replace('Z', '')).replace(tzinfo=NY)) if t else iso(now)
        except ValueError:
            ts = iso(now)
        ext[sym] = clean({'p': price, 't': ts, 's': 'regular', 'pc': fnum(d.get('prev_day_close')),
                          'h': fnum(d.get('high')), 'l': fnum(d.get('low')), 'v': 0})
    intraday = []
    if price and sym in ext:
        tsq = ext[sym]['t']
        intraday = [[tsq, price, price, price, price, 0]]
        rows = [r for r in load(f'intraday/{sym}.json', []) if isinstance(r, list)]
        if not rows or rows[-1][0] != tsq:
            rows.append(intraday[0])
            cutoff = iso(now - timedelta(days=10))
            save(f'intraday/{sym}.json', [r for r in rows if r[0] >= cutoff][-5000:])
    ext_doc['sym'] = ext
    save('ext.json', ext_doc)
    log('VIX:', price, 'intraday bodů', len(intraday))
    return [sym] if price or os.path.exists(os.path.join(DATA, 'bars', f'{sym}.json')) else []


def get_json_h(url, headers):
    b = http(url, headers=headers)
    try:
        return json.loads(b) if b else None
    except ValueError:
        return None


def parse_bar_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def time_adjusted_volume(sym, current, volume, avg_volume):
    """Porovná dnešní objem s průměrem ve stejnou část obchodního dne.

    Denní objem dělený celodenním průměrem je během seance zavádějící. Pro
    akcie proto používáme několik předchozích minutových seancí do stejného
    času; pro krypto totéž v rámci UTC dne. Před otevřením amerického trhu
    raději RVOL nezobrazíme, než abychom z premarketu vyráběli falešný signál.
    """
    if not avg_volume:
        return None, None, 'unavailable'
    session = current.get('s')
    if session == 'post':
        return volume / avg_volume, avg_volume, 'full_day'
    ts = parse_bar_time(current.get('t'))
    if not ts:
        return None, None, 'unavailable'
    is_crypto = sym.endswith('-USD')
    local_now = ts.astimezone(timezone.utc if is_crypto else NY)
    if not is_crypto:
        minute = local_now.hour * 60 + local_now.minute
        if session == 'pre' or minute < 9 * 60 + 30:
            return None, None, 'premarket'
        if minute >= 16 * 60:
            return volume / avg_volume, avg_volume, 'full_day'
        start_minute = 9 * 60 + 30
    else:
        minute = local_now.hour * 60 + local_now.minute
        start_minute = 0

    rows = load(f'intraday/{sym}.json', [])
    by_day = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        dt = parse_bar_time(row[0])
        if not dt:
            continue
        dt = dt.astimezone(timezone.utc if is_crypto else NY)
        row_minute = dt.hour * 60 + dt.minute
        if start_minute <= row_minute <= minute:
            by_day[dt.date().isoformat()] = by_day.get(dt.date().isoformat(), 0) + (row[5] or 0)
    today = local_now.date().isoformat()
    actual = by_day.pop(today, None)
    previous = [v for _, v in sorted(by_day.items())[-5:] if v > 0]
    if actual is not None and previous:
        expected = sum(previous) / len(previous)
        return (actual / expected if expected else None), expected, 'same_time'

    # Nově přidaný titul ještě nemusí mít několik minulých minutových seancí.
    # Dočasný odhad je výslovně označený a po backfillu se sám nahradí.
    elapsed = max(1, minute - start_minute + 1)
    session_minutes = 1440 if is_crypto else 390
    expected = avg_volume * min(1, elapsed / session_minutes)
    return (volume / expected if expected else None), expected, 'estimated'


def build_market_snapshot(symbols):
    """Připraví jeden veřejný snapshot pro web bez klientského API klíče."""
    ext_doc = load('ext.json', {})
    ext = ext_doc.get('sym', {})
    names = load('tickers.json', {})
    buys = load('feed-buys.json', [])
    buy_counts = {}
    for row in buys:
        ticker = row.get('t')
        if ticker:
            buy_counts[ticker] = buy_counts.get(ticker, 0) + 1
    out = []
    for sym in symbols:
        bars = load(f'bars/{sym}.json', [])
        bars = [b for b in bars if isinstance(b, list) and len(b) >= 6]
        current = ext.get(sym, {})
        last = bars[-1] if bars else None
        previous = bars[-2] if len(bars) > 1 else None
        price = current.get('p') or (last[4] if last else None)
        prev_close = current.get('pc') or (previous[4] if previous else None)
        if price is None:
            continue
        completed_day = last if last and last[0] == datetime.now(NY).date().isoformat() else None
        if current.get('s') == 'post' and completed_day:
            day_high, day_low, volume = completed_day[2], completed_day[3], completed_day[5]
        else:
            day_high = current.get('h') or (last[2] if last else price)
            day_low = current.get('l') or (last[3] if last else price)
            volume = current.get('v') or (last[5] if last else 0)
        history = bars[-252:]
        completed = bars[-21:-1] if len(bars) > 1 else bars[-20:]
        avg_volume = sum(b[5] or 0 for b in completed) / len(completed) if completed else None
        rel_volume, expected_volume, rvol_basis = time_adjusted_volume(
            sym, current, volume, avg_volume)
        name_data = names.get(sym) or []
        name = CFG.get('names', {}).get(sym) or (name_data[1] if len(name_data) > 1 else sym)
        out.append(clean({
            'ticker': sym, 'name': name, 'price': price, 'previous_close': prev_close,
            'change': ((price - prev_close) / prev_close * 100) if prev_close else None,
            'day_high': day_high, 'day_low': day_low, 'volume': volume,
            'avg_volume': avg_volume, 'rel_volume': rel_volume,
            'expected_volume': expected_volume, 'rvol_basis': rvol_basis,
            'year_high': max((b[2] for b in history), default=day_high),
            'year_low': min((b[3] for b in history), default=day_low),
            'insider_buys': buy_counts.get(sym, 0),
            'timestamp': current.get('t') or (last[0] if last else None),
        }))
    out.sort(key=lambda row: row.get('volume') or 0, reverse=True)
    save('market.json', {
        'updated': ext_doc.get('updated') or NOW.isoformat(), 'delay': ext_doc.get('delay', 15),
        'source': 'Alpaca + SEC EDGAR', 'symbols': out,
    })
    log('tržní snapshot:', len(out), 'titulů')


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
# ---------------------------------------------------------------- cílové ceny analytiků (Nasdaq)
def update_targets(symbols):
    hdr = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
           'Accept': 'application/json, text/plain, */*', 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}
    done = 0
    for sym in symbols:
        if not time_left():
            break
        old = load(f'targets/{sym}.json', None)
        if old and old.get('updated', '')[:10] == TODAY.isoformat():
            continue
        b = http(f'https://api.nasdaq.com/api/analyst/{urllib.parse.quote(sym)}/targetprice', headers=hdr)
        if not b:
            continue
        try:
            data = json.loads(b).get('data') or {}
        except ValueError:
            continue
        co = data.get('consensusOverview') or {}
        out = {'t': sym, 'updated': NOW.isoformat(),
               'low': fnum(co.get('lowPriceTarget')), 'high': fnum(co.get('highPriceTarget')),
               'avg': fnum(co.get('priceTarget')),
               'buy': int(co.get('buy') or 0), 'hold': int(co.get('hold') or 0), 'sell': int(co.get('sell') or 0)}
        if not out['avg']:
            out = {'t': sym, 'updated': NOW.isoformat(), 'none': True}
        save(f'targets/{sym}.json', out)
        done += 1
        time.sleep(0.3)
    if done:
        log('cílové ceny analytiků:', done, 'titulů')


def main():
    os.makedirs(DATA, exist_ok=True)
    meta = load('meta.json', {})
    legacy_options = os.path.join(DATA, 'options.json')
    if os.path.exists(legacy_options):
        os.unlink(legacy_options)
    meta.pop('options_at', None)
    tickers = [t.upper() for t in CFG.get('tickers', [])]
    market_symbols = list(dict.fromkeys(tickers + [t.upper() for t in CFG.get('market_symbols', [])]))

    crypto_pairs = [p.upper() for p in CFG.get('crypto', [])]
    crypto_syms = [p.replace('/', '-') for p in crypto_pairs]
    all_symbols = market_symbols + crypto_syms + ['VIX']
    for step in (lambda: update_alpaca(meta, market_symbols), lambda: update_crypto(meta, crypto_pairs),
                 lambda: update_vix(meta),
                 lambda: build_market_snapshot(all_symbols), lambda: update_fx(meta)):
        try:
            step()
        except Exception as e:
            log('CHYBA:', repr(e))

    # Pětiminutový běh obnovuje jen ceny a grafy. SEC, 13F a fundamenty jsou
    # výrazně pomalejší a spouštějí se samostatným plným během.
    if MARKET_ONLY:
        meta['alpaca'] = bool(AK and AS)
        meta['market_at'] = load('market.json', {}).get('updated') or datetime.now(timezone.utc).isoformat()
        meta['updated'] = datetime.now(timezone.utc).isoformat()
        save('meta.json', meta)
        save('debug.json', DEBUG)
        log('rychlá aktualizace hotová za', round(time.time() - START), 's')
        return

    if meta.get('feed', {}).get('buys', 0) == 0 and meta.get('feed', {}).get('sells', 0) == 0:
        meta.pop('backfilled', None)  # historie se zatím nestáhla
    sec_ok = probe_sec()
    if not sec_ok:
        log('SEC odmítá dotazy:', DEBUG['probe'])
        meta['error'] = 'SEC odmítá dotazy'
    else:
        meta.pop('error', None)
    tmap = ticker_map(meta) if sec_ok else load('tickers.json', {})
    rev = {}
    for t, (cik, _) in tmap.items():
        rev.setdefault(str(int(cik)), t)
    log('tickerů v SEC:', len(tmap))

    # Výkazy a 8-K stačí obnovit jednou denně. Tržní cena se do ocenění
    # propíše při tomto denním snapshotu; návštěvník žádný klíč nepotřebuje.
    missing_fundamentals = [sym for sym in tickers
                            if not os.path.exists(os.path.join(DATA, 'fundamentals', f'{sym}.json'))]
    if (meta.get('fundamentals_at', '')[:10] != TODAY.isoformat()
            or meta.get('fundamentals_schema') != 2 or missing_fundamentals):
        market_prices = {r.get('ticker'): r.get('price') for r in load('market.json', {}).get('symbols', [])}
        fundamental_ok = 0
        targets = tickers if meta.get('fundamentals_at', '')[:10] != TODAY.isoformat() else (missing_fundamentals or tickers)
        for sym in targets:
            if not time_left():
                break
            try:
                got_fundamentals = update_fundamentals(sym, tmap, market_prices.get(sym)) if sec_ok else False
                if not got_fundamentals:
                    got_fundamentals = update_fundamentals_nasdaq(sym, market_prices.get(sym))
                fundamental_ok += bool(got_fundamentals)
            except Exception as e:
                log('CHYBA fundamenty', sym, repr(e))
        if fundamental_ok:
            meta['fundamentals_at'] = NOW.isoformat()
            meta['fundamentals_refreshed'] = fundamental_ok
            meta['fundamentals_count'] = sum(
                os.path.exists(os.path.join(DATA, 'fundamentals', f'{sym}.json')) for sym in tickers)
            meta['fundamentals_schema'] = 2

    try:
        update_targets(tickers)
    except Exception as e:
        log('CHYBA cílové ceny:', repr(e))

    # Zbytek sběru (Form 4, 8-K, 13F) vyžaduje SEC. Když SEC blokuje
    # GitHub runner, zachováme starší SEC data, ale fundamenty z Nasdaq už jsou uložené.
    if not sec_ok:
        meta['market_at'] = load('market.json', {}).get('updated') or datetime.now(timezone.utc).isoformat()
        meta['updated'] = datetime.now(timezone.utc).isoformat()
        save('meta.json', meta)
        save('debug.json', DEBUG)
        return

    try:
        update_feed(meta)
        meta['feed_at'] = datetime.now(timezone.utc).isoformat()
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
    try:
        build_market_snapshot(all_symbols)
    except Exception as e:
        log('CHYBA tržní snapshot:', repr(e))
    meta['alpaca'] = bool(AK and AS)
    meta['notify'] = bool(TOPIC)
    meta['market_at'] = load('market.json', {}).get('updated') or datetime.now(timezone.utc).isoformat()
    meta['updated'] = datetime.now(timezone.utc).isoformat()
    save('meta.json', meta)
    save('debug.json', DEBUG)
    log('hotovo za', round(time.time() - START), 's')


if __name__ == '__main__':
    sys.exit(main())
