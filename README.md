# MarketTrace

Živé ceny akcií (Finnhub), obchody insiderů (SEC Form 4) a portfolia guru investorů (SEC 13F).

- `index.html` – celá aplikace
- `config.json` – sledované tickery a guru investoři (upravte a uložte, projeví se při dalším běhu)
- `scripts/update.py` – sběr dat ze SEC (Form 4, Form 144, 13D, 13F), Alpaca (grafy, ceny mimo hlavní seanci) a kurzu ČNB; spouští ho GitHub Actions každých 20 minut
- Alpaca klíče patří do Settings → Secrets and variables → Actions jako `ALPACA_KEY` a `ALPACA_SECRET`
- `data/` – stažená data (neupravovat ručně)
