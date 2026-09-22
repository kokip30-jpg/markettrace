#!/usr/bin/env python3
"""Kontrola úplnosti lokálních dat a základní smoke test GitHub Pages."""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_bars(rows, name, minimum=1):
    require(isinstance(rows, list) and len(rows) >= minimum,
            f'{name}: očekáváno alespoň {minimum} svíček, nalezeno {len(rows) if isinstance(rows, list) else 0}')
    for row in rows[-5:]:
        require(isinstance(row, list) and len(row) >= 6, f'{name}: neplatný formát svíčky')


def validate_local():
    cfg = read_json(os.path.join(ROOT, 'config.json'))
    public_text = json.dumps(cfg, ensure_ascii=False).lower()
    require('admin@masaze-tisnov.cz' not in public_text, 'config.json zveřejňuje osobní e-mail')
    tickers = [x.upper() for x in cfg.get('tickers', [])]
    market_symbols = [x.upper() for x in cfg.get('market_symbols', [])]
    crypto = [x.upper().replace('/', '-') for x in cfg.get('crypto', [])]
    stocks = list(dict.fromkeys(tickers + market_symbols))
    require(len(tickers) >= 100, f'konfigurace obsahuje jen {len(tickers)} akcií')

    market = read_json(os.path.join(DATA, 'market.json'))
    found = {x.get('ticker') for x in market.get('symbols', [])}
    expected = set(stocks + crypto + ['VIX'])
    missing_market = sorted(expected - found)
    require(not missing_market, 'market.json postrádá: ' + ', '.join(missing_market))

    for symbol in stocks + crypto:
        bars_path = os.path.join(DATA, 'bars', f'{symbol}.json')
        intraday_path = os.path.join(DATA, 'intraday', f'{symbol}.json')
        require(os.path.exists(bars_path), f'chybí denní data {symbol}')
        require(os.path.exists(intraday_path), f'chybí minutová data {symbol}')
        validate_bars(read_json(bars_path), f'bars/{symbol}')
        validate_bars(read_json(intraday_path), f'intraday/{symbol}')
    for symbol in stocks:
        hourly_path = os.path.join(DATA, 'hourly', f'{symbol}.json')
        require(os.path.exists(hourly_path), f'chybí hodinová data {symbol}')
        validate_bars(read_json(hourly_path), f'hourly/{symbol}')

    validate_bars(read_json(os.path.join(DATA, 'hourly', 'BMNR.json')), 'hourly/BMNR', 200)
    fundamental_count = sum(os.path.exists(os.path.join(DATA, 'fundamentals', f'{symbol}.json')) for symbol in tickers)
    require(fundamental_count >= int(len(tickers) * .9),
            f'fundamenty jsou jen pro {fundamental_count}/{len(tickers)} titulů')
    print(f'OK: {len(found)} tržních symbolů, {len(stocks)} akciových hodinových sad, {fundamental_count} fundamentů')


def fetch_json(base, path):
    url = base.rstrip('/') + '/' + path
    with urllib.request.urlopen(url, timeout=30) as response:
        require(response.status == 200, f'{path}: HTTP {response.status}')
        return json.load(response)


def fetch_text(base, path):
    url = base.rstrip('/') + '/' + path
    with urllib.request.urlopen(url, timeout=30) as response:
        require(response.status == 200, f'{path}: HTTP {response.status}')
        return response.read().decode('utf-8', 'replace')


def validate_remote(base):
    last_error = None
    for attempt in range(5):
        try:
            index = fetch_text(base, f'?smoke={int(time.time())}')
            config = fetch_json(base, f'config.json?smoke={int(time.time())}')
            market = fetch_json(base, f'data/market.json?smoke={int(time.time())}')
            meta = fetch_json(base, f'data/meta.json?smoke={int(time.time())}')
            bmnr = fetch_json(base, f'data/hourly/BMNR.json?smoke={int(time.time())}')
            debug = fetch_json(base, f'data/debug.json?smoke={int(time.time())}')
            require(re.search(r'app\.js\?v=20260922-(?:1[5-9]|[2-9]\d)', index), 'produkce nemá očekávanou verzi app.js')
            require('admin@masaze-tisnov.cz' not in json.dumps([config, debug]).lower(), 'produkce zveřejňuje osobní e-mail')
            require(len(market.get('symbols', [])) >= 120, 'produkční market.json je neúplný')
            require(meta.get('market_at') or market.get('updated'), 'chybí čas aktualizace cen')
            validate_bars(bmnr, 'produkční hourly/BMNR', 200)
            print(f'OK produkce: {len(market.get("symbols", []))} symbolů, BMNR {len(bmnr)} hodinových svíček')
            return
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(5)
    raise last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url')
    args = parser.parse_args()
    try:
        validate_remote(args.base_url) if args.base_url else validate_local()
    except Exception as exc:
        print(f'CHYBA VALIDACE: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
