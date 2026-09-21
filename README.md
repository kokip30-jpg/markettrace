# MarketTrace

Živé ceny akcií (Finnhub), obchody insiderů (SEC Form 4) a portfolia guru investorů (SEC 13F).

- `index.html` – celá aplikace
- `config.json` – sledované tickery a guru investoři (upravte a uložte, projeví se při dalším běhu)
- `scripts/update.py` – sběr dat ze SEC, spouští ho GitHub Actions každých 20 minut
- `data/` – stažená data (neupravovat ručně)
