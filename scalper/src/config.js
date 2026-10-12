import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = path.resolve(projectRoot, '..');

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const at = trimmed.indexOf('=');
    if (at < 1) continue;
    const key = trimmed.slice(0, at).trim();
    const value = trimmed.slice(at + 1).trim().replace(/^['"]|['"]$/g, '');
    if (!(key in process.env)) process.env[key] = value;
  }
}

// MarketTrace a scalper pouzivaji jednu sadu klicu. GitHub Actions je poskytuje
// jako ALPACA_KEY / ALPACA_SECRET; puvodni nazvy Alpaca SDK zustavaji podporovane.
loadEnv(path.join(repositoryRoot, '.env'));
loadEnv(path.join(projectRoot, '.env'));
if (process.env.ALPACA_SHARED_ENV) loadEnv(process.env.ALPACA_SHARED_ENV);
const number = (name, fallback) => Number.isFinite(Number(process.env[name])) ? Number(process.env[name]) : fallback;

export const config = Object.freeze({
  port: number('PORT', 4310),
  key: process.env.ALPACA_KEY || process.env.APCA_API_KEY_ID || '',
  secret: process.env.ALPACA_SECRET || process.env.APCA_API_SECRET_KEY || '',
  feed: process.env.APCA_DATA_FEED || 'iex',
  watchlist: (process.env.WATCHLIST || 'SPY,QQQ').split(',').map(s => s.trim().toUpperCase()).filter(Boolean),
  autoExecute: process.env.AUTO_EXECUTE === 'true',
  riskPerTradePct: number('RISK_PER_TRADE_PCT', 0.10),
  maxDailyLossPct: number('MAX_DAILY_LOSS_PCT', 0.75),
  maxTradesPerDay: number('MAX_TRADES_PER_DAY', 6),
  maxSpreadBps: number('MAX_SPREAD_BPS', 8),
  stopAtrMultiplier: number('STOP_ATR_MULTIPLIER', 1),
  targetRMultiplier: number('TARGET_R_MULTIPLIER', 1.5),
  cooldownMinutes: number('COOLDOWN_MINUTES', 10),
  paperBase: 'https://paper-api.alpaca.markets',
  dataBase: 'https://data.alpaca.markets',
});

export const hasCredentials = () => Boolean(config.key && config.secret);
