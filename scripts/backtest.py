#!/usr/bin/env python3
"""Zpětný test strategie „kup po nákupu insidera".

Pravidlo: insider koupí na volném trhu (Form 4, kód P). Následující obchodní den
po zveřejnění hlášení nakoupíme za závěrečnou cenu a držíme daný počet dní,
případně dřív vystoupíme na stop-lossu. Porovnáváme s SPY za stejné období.

Spuštění:  python3 scripts/backtest.py [složka_s_daty]
"""
import json
import os
import statistics
import sys
from datetime import datetime, timedelta

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
HORIZONS = (30, 60, 90)
STOP_LOSS = 0.15
MIN_VALUE = 25_000
EXEC_WORDS = ('CEO', 'CHIEF', 'PRESIDENT', 'CHAIR', 'FOUNDER', 'CFO', 'COO', 'CTO')


def load(path, default=None):
    try:
        with open(os.path.join(DATA, path), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def bars_map(sym):
    rows = load(f'bars/{sym}.json', []) or []
    return [r for r in rows if isinstance(r, list) and len(r) >= 5 and r[4]]


def close_on_or_after(bars, day):
    for b in bars:
        if b[0] >= day:
            return b[0], b[4]
    return None, None


def close_on_or_before(bars, day):
    prev = None
    for b in bars:
        if b[0] > day:
            break
        prev = b
    return (prev[0], prev[4]) if prev else (None, None)


def path_low(bars, start, end):
    lows = [b[3] for b in bars if start <= b[0] <= end and len(b) > 3 and b[3]]
    return min(lows) if lows else None


def build_events():
    events = []
    for name in sorted(os.listdir(os.path.join(DATA, 't'))):
        sym = name[:-5]
        rows = (load(f't/{name}', {}) or {}).get('tx') or []
        deals = {}
        for r in rows:
            if r.get('k') != 'P' or not r.get('d') or not r.get('p'):
                continue
            key = (r.get('a'), r.get('o'))
            d = deals.setdefault(key, {'sym': sym, 'owner': r.get('o') or '', 'role': (r.get('r') or '').upper(),
                                       'filed': r.get('f'), 'date': r.get('d'), 'shares': 0.0, 'value': 0.0,
                                       'held': r.get('h'), 'exec': bool(r.get('of')), 'dir': bool(r.get('dr'))})
            d['shares'] += r.get('s') or 0
            d['value'] += (r.get('s') or 0) * (r.get('p') or 0)
            if (r.get('d') or '') >= d['date']:
                d['date'] = r['d']
                d['held'] = r.get('h')
        events.extend(deals.values())
    # cluster: kolik různých insiderů nakoupilo ve stejné firmě do 14 dní
    by_sym = {}
    for e in events:
        by_sym.setdefault(e['sym'], []).append(e)
    for group in by_sym.values():
        for e in group:
            t0 = datetime.fromisoformat(e['date'])
            e['cluster'] = len({x['owner'] for x in group
                                if abs((datetime.fromisoformat(x['date']) - t0).days) <= 14})
    for e in events:
        stake = None
        if e['held'] and e['shares'] and e['held'] > e['shares']:
            stake = e['shares'] / (e['held'] - e['shares']) * 100
        e['stake'] = stake
        e['is_exec'] = any(w in e['role'] for w in EXEC_WORDS)
        score = 1
        score += 3 if e['is_exec'] else 1 if e['dir'] else 0
        score += 2 if e['value'] >= 1e6 else 1.5 if e['value'] >= 250e3 else 1 if e['value'] >= 100e3 else 0
        score += 2 if e['cluster'] >= 3 else 1 if e['cluster'] == 2 else 0
        score += 2 if (stake or 0) >= 50 else 1.5 if (stake or 0) >= 20 else 1 if (stake or 0) >= 10 else 0
        e['score'] = max(1, min(10, round(score)))
    return events


def simulate(events, spy, horizon, use_stop):
    trades = []
    for e in events:
        if e['value'] < MIN_VALUE or not e.get('filed'):
            continue
        bars = bars_map(e['sym'])
        if not bars:
            continue
        entry_day, entry = close_on_or_after(bars, e['filed'])
        if not entry:
            continue
        target = (datetime.fromisoformat(entry_day) + timedelta(days=horizon)).date().isoformat()
        if target > bars[-1][0]:
            continue  # období ještě neuplynulo
        exit_day, exit_px = close_on_or_before(bars, target)
        stopped = False
        if use_stop:
            low = path_low(bars, entry_day, target)
            if low and low <= entry * (1 - STOP_LOSS):
                exit_px, stopped = entry * (1 - STOP_LOSS), True
        ret = (exit_px / entry - 1) * 100
        s_entry = close_on_or_after(spy, entry_day)[1]
        s_exit = close_on_or_before(spy, target)[1]
        bench = (s_exit / s_entry - 1) * 100 if s_entry and s_exit else None
        trades.append({**e, 'ret': ret, 'bench': bench, 'alpha': None if bench is None else ret - bench, 'stopped': stopped})
    return trades


def stats(trades, label):
    if not trades:
        return f'{label}: bez obchodů'
    rets = [t['ret'] for t in trades]
    alphas = [t['alpha'] for t in trades if t['alpha'] is not None]
    win = sum(1 for r in rets if r > 0) / len(rets) * 100
    beat = sum(1 for a in alphas if a > 0) / len(alphas) * 100 if alphas else 0
    return (f'{label}: {len(trades)} obchodů | medián {statistics.median(rets):+.1f} % | průměr {statistics.fmean(rets):+.1f} % | '
            f'v plusu {win:.0f} % | proti SPY {statistics.median(alphas):+.1f} % (medián), lepších než SPY {beat:.0f} % | '
            f'nejhorší {min(rets):+.1f} % / nejlepší {max(rets):+.1f} %')


def main():
    events = build_events()
    spy = bars_map('SPY')
    print(f'Nákupů insiderů v datech: {len(events)} (sledované tituly, poslední rok)')
    print(f'Stop-loss {int(STOP_LOSS*100)} %, minimální velikost nákupu {MIN_VALUE:,} $'.replace(',', ' '))
    for horizon in HORIZONS:
        print(f'\n=== Držení {horizon} dní ===')
        base = simulate(events, spy, horizon, use_stop=False)
        print(stats(base, 'Vše'))
        print(stats(simulate(events, spy, horizon, use_stop=True), f'Vše se stop-lossem {int(STOP_LOSS*100)} %'))
        for lo in (6, 8):
            print(stats([t for t in base if t['score'] >= lo], f'Skóre {lo}+'))
        print(stats([t for t in base if t['is_exec']], 'Jen vedení firmy'))
        print(stats([t for t in base if t['cluster'] >= 2], 'Skupinové nákupy'))
        print(stats([t for t in base if t['value'] >= 250_000], 'Nákup nad 250 tis. $'))
    print('\nPozor: vzorek pokrývá jen poslední rok a sledované tituly, výsledky jsou orientační.')


if __name__ == '__main__':
    main()
