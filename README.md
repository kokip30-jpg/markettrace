# MarketTrace

Veřejná česká aplikace pro sledování amerických akcií, insider obchodů a portfolií známých investorů.

## Jak fungují data

Návštěvník aplikace nezadává žádný API klíč. GitHub Actions připravuje datový snapshot na serveru a GitHub Pages ho pouze zobrazí.

- ceny, objemy a denní svíčky: Alpaca, přibližně 15 minut zpoždění;
- interaktivní minutové a svíčkové grafy: veřejný widget TradingView bez uživatelského API klíče;
- obchody insiderů: SEC EDGAR Form 4;
- plánované prodeje: SEC Form 144;
- aktivistické podíly: SEC Schedule 13D;
- portfolia guru investorů: SEC Form 13F;
- kurz USD/CZK: Česká národní banka.

## Analytické funkce

- vysvětlitelné MarketTrace skóre rozdělené na trend, momentum, objem, Smart Money a stabilitu;
- pokročilý screener podle relativního objemu, insider nákupů, 52týdenního pásma a skóre;
- časová osa cenových a SEC událostí v detailu akcie;
- cluster nákupy více insiderů z jedné společnosti;
- porovnání dvou až čtyř akcií;
- Smart Money přehled nových, navýšených, snížených a ukončených 13F pozic.

Automat běží v pracovní dny každých 5 minut a o víkendu každé 2 hodiny. GitHub může naplánovaný běh při vytížení opozdit.

## Struktura

- `index.html` – přístupná struktura aplikace;
- `styles.css` – responzivní světlý a tmavý vzhled;
- `app.js` – screener, detail titulu, insideři, guru a lokální portfolio;
- `config.json` – sledované tituly a investoři;
- `scripts/update.py` – serverová příprava dat;
- `.github/workflows/update.yml` – pravidelná aktualizace a nasazení.

## Tajné proměnné GitHub Actions

- `ALPACA_KEY`
- `ALPACA_SECRET`
- volitelně `NTFY_TOPIC` pro administrátorská upozornění

Portfolio a watchlist se ukládají pouze do prohlížeče návštěvníka. Portfolio lze exportovat a znovu importovat jako JSON.
