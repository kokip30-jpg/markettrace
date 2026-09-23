# Osobní účty — připraveno k dokončení

Supabase projekt: `rftgfskqvyhdgirhwmvm` (MarketTrace, Frankfurt).

Přihlášení používá Supabase Auth s heslem a ručně založenými účty. Veřejná registrace ani odesílání e-mailů není součástí této verze. Uživatelské jméno `test` se mapuje na technický identifikátor `test@accounts.markettrace.invalid`; nejde o doručovací adresu. Účet je potřeba založit v Auth administraci s potvrzenou adresou. Heslo se do repozitáře neukládá. Reset hesla u technického účtu provádí správce.

Databáze obsahuje tabulku `public.user_preferences` s primárním klíčem `(user_id, key)`, odkazem na `auth.users` s ON DELETE CASCADE a JSONB hodnotou. Migrace `create_private_user_preferences` je uložená v Supabase. RLS dovoluje SELECT/INSERT/UPDATE/DELETE pouze vlastníkovi podle `auth.uid()`. Anonymní role nemá oprávnění k tabulce. SQL test s dvěma dočasnými uživateli ověřil oddělení čtení, zápisu a mazání i zákaz převodu vlastnictví; transakce byla vrácena zpět.

Klient používá jen veřejný publishable klíč. Hesla ověřuje Supabase, nikoli JavaScript webu. Nastavení se načítá před spuštěním aplikace. Data návštěvníka bez přihlášení se nepřevádějí do účtu. Neodeslané změny se zálohují pod UUID účtu a opakují po připojení. Zápisy probíhají po jednotlivých klíčích. Při souběžné změně stejného klíče na více zařízeních vyhraje poslední zápis; nejedná se o slučování jednotlivých obchodů portfolia.

Ukládá se portfolio, watchlist, porovnání, motiv, výška grafu, interval a indikátor. Stav uložení je v dialogu účtu. Bez přihlášení funguje původní místní úložiště. Při chybě prvotního načtení účtu se aplikace nespustí s prázdnými hodnotami, aby nepřepsala osobní nastavení.

## Vývoj

`npm ci` a `npm run build:accounts` vytvoří statický `accounts.js`; tento soubor se publikuje na GitHub Pages spolu s ostatními soubory. `npm test` ověřuje frontu zápisů, obnovení po výpadku a oddělení účtů. Závislosti mají pevné verze a lockfile.

## Zbývá před nasazením

1. Založit testovací účet běžnou Auth administrací (nutné přihlášení správce).
2. Ověřit skutečné přihlášení a odhlášení, uložení a nové načtení nastavení na druhém zařízení.
3. Ověřit formulář na desktopu a mobilu, poté nasadit na main a zkontrolovat Pages.

Automatická kontrola odmítla dočasnou administrační Edge Function; nebyla nasazena. Žádné přihlašovací údaje ani privilegovaný klíč nejsou ve veřejných souborech.
