# MarketTrace Alpaca Paper Scalper

Scalper je v podslozce `scalper/` repozitare MarketTrace. Pouziva stejne
promenne `ALPACA_KEY` a `ALPACA_SECRET` jako aktualizace MarketTrace; zadne druhe
klice nejsou potreba. Stale podporuje i puvodni lokalni nazvy
`APCA_API_KEY_ID` a `APCA_API_SECRET_KEY`.

Z korene MarketTrace jej spustite prikazem `npm run scalper` a testy prikazem
`npm run scalper:test`. Pro lokalni beh lze hodnoty ulozit do korenoveho `.env`
nebo do `scalper/.env`; oba soubory zustavaji ignorovane Gitem.

Samostatný lokální projekt pro sledování krátkodobých akciových signálů a ručně potvrzované bracket objednávky do **Alpaca Paper Trading**. Nezasahuje do OptionScope.

## Bezpečnostní režim

- Používá výhradně `https://paper-api.alpaca.markets`.
- Výchozí `AUTO_EXECUTE=false`; každý obchod vyžaduje ruční potvrzení slovem `PAPER`.
- API klíče zůstávají pouze na backendu v ignorovaném souboru `.env`.
- STOP zruší otevřené objednávky a zastaví další vstupy.
- Velikost pozice omezuje risk na obchod i 20 % buying power.

## Spuštění

1. Pro lokální běh vložte **paper** Key ID a Secret Key do kořenového `.env` pod názvy `ALPACA_KEY` a `ALPACA_SECRET`.
2. Z kořene MarketTrace spusťte `npm run scalper`.
3. Otevřete `http://127.0.0.1:4310`.

V GitHub Actions se automaticky použijí již existující MarketTrace secrets `ALPACA_KEY` a `ALPACA_SECRET`.

Není potřeba instalovat žádné balíčky; vyžaduje Node.js 22+.

## Logika první verze

Sleduje SPY a QQQ na minutových svíčkách. Signál vyžaduje současně trend EMA 9/20, správnou stranu VWAP, průraz předchozí svíčky, relativní objem alespoň 1,15× a přijatelný spread. Stop je odvozen z ATR a profit target má výchozí poměr 1,5R.

Bezplatný IEX feed Alpacy je jen část amerického trhu. Pro přesnější scalping lze v `.env` změnit `APCA_DATA_FEED=sip`, pokud má účet příslušné předplatné.

## Testy

```powershell
npm test
```

Paper výsledky nejsou zárukou výsledků na živém účtu. Strategie je experimentální a před případným živým použitím vyžaduje delší forward test.
