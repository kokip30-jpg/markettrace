import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import * as alpaca from './alpaca.js';
import { buildTradePlan, evaluateSignal } from './strategy.js';

const state = {
  status: 'Čekám na připojení', connected: false, halted: false, account: null, clock: null,
  symbols: {}, positions: [], orders: [], tradesToday: 0, realizedToday: 0, events: [], updatedAt: null,
  settings: { ...config },
};
const listeners = new Set();
const lastTradeAt = new Map();
const journal = path.resolve('data', 'journal.jsonl');

function publicState() {
  return { ...state, settings: {
    watchlist: config.watchlist, autoExecute: config.autoExecute, riskPerTradePct: config.riskPerTradePct,
    maxDailyLossPct: config.maxDailyLossPct, maxTradesPerDay: config.maxTradesPerDay,
    maxSpreadBps: config.maxSpreadBps, stopAtrMultiplier: config.stopAtrMultiplier,
    targetRMultiplier: config.targetRMultiplier, cooldownMinutes: config.cooldownMinutes, feed: config.feed,
  }};
}
function emit() { state.updatedAt = new Date().toISOString(); for (const fn of listeners) fn(publicState()); }
function event(type, message, detail = null) {
  const item = { at: new Date().toISOString(), type, message, detail };
  state.events.unshift(item); state.events = state.events.slice(0, 60);
  fs.appendFileSync(journal, `${JSON.stringify(item)}\n`); emit();
}
function settings() { return config; }

function riskGate(symbol) {
  if (state.halted) return 'Systém je zastavený.';
  if (!state.clock?.is_open) return 'Americký trh je zavřený.';
  if (state.tradesToday >= config.maxTradesPerDay) return 'Dosažen maximální počet obchodů.';
  const maxLoss = Number(state.account?.last_equity || state.account?.equity || 0) * config.maxDailyLossPct / 100;
  if (state.realizedToday <= -maxLoss) return 'Dosažen denní limit ztráty.';
  if (state.positions.some(p => p.symbol === symbol) || state.orders.some(o => o.symbol === symbol)) return 'Pro symbol už existuje pozice nebo objednávka.';
  const last = lastTradeAt.get(symbol) || 0;
  if (Date.now() - last < config.cooldownMinutes * 60000) return 'Symbol je v cooldownu.';
  return null;
}

async function refreshAccount() {
  [state.account, state.clock, state.positions, state.orders] = await Promise.all([
    alpaca.getAccount(), alpaca.getClock(), alpaca.getPositions(), alpaca.getOrders(),
  ]);
  state.connected = true;
}

async function consider(symbol) {
  const item = state.symbols[symbol];
  if (!item) return;
  const signal = { ...evaluateSignal(item.bars, item.quote, config), symbol };
  item.signal = signal;
  item.plan = buildTradePlan({ signal, equity: Number(state.account?.equity || 0), buyingPower: Number(state.account?.buying_power || 0), settings: config });
  const blocked = riskGate(symbol);
  item.blocked = blocked;
  if (!config.autoExecute || !item.plan || blocked) return emit();
  try {
    const order = await alpaca.submitBracket(item.plan);
    state.tradesToday += 1; lastTradeAt.set(symbol, Date.now());
    event('order', `Paper bracket objednávka odeslána: ${symbol}`, { orderId: order.id, plan: item.plan });
    await refreshAccount();
  } catch (error) { event('error', `Objednávka ${symbol} selhala: ${error.message}`); }
}

export async function start() {
  fs.mkdirSync(path.dirname(journal), { recursive: true });
  try {
    await refreshAccount();
    for (const symbol of config.watchlist) {
      state.symbols[symbol] = { bars: (await alpaca.getBars(symbol, 100)).slice(-100), quote: null, signal: null, plan: null };
    }
    alpaca.connectMarketStream({
      symbols: config.watchlist,
      onStatus: value => { state.status = value; emit(); },
      onQuote: quote => { state.symbols[quote.S].quote = { bid: quote.bp, ask: quote.ap, at: quote.t }; emit(); },
      onBar: bar => {
        const item = state.symbols[bar.S];
        item.bars.push({ t: bar.t, o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v });
        item.bars = item.bars.slice(-120);
        consider(bar.S);
      },
    });
    state.status = 'Účet připojen, čekám na stream'; emit();
    setInterval(() => refreshAccount().then(emit).catch(e => event('error', e.message)), 15000).unref();
  } catch (error) { state.status = `Připojení selhalo: ${error.message}`; event('error', state.status); }
}

export function subscribe(fn) { listeners.add(fn); fn(publicState()); return () => listeners.delete(fn); }
export const getState = publicState;
export async function execute(symbol) {
  const item = state.symbols[symbol];
  if (!item?.plan) throw new Error('Pro symbol není připraven platný plán.');
  const blocked = riskGate(symbol); if (blocked) throw new Error(blocked);
  const order = await alpaca.submitBracket(item.plan);
  state.tradesToday += 1; lastTradeAt.set(symbol, Date.now());
  event('order', `Ručně potvrzená paper objednávka: ${symbol}`, { orderId: order.id, plan: item.plan });
  await refreshAccount(); emit(); return order;
}
export async function emergencyStop(closePositions = false) {
  state.halted = true;
  await alpaca.cancelAllOrders();
  if (closePositions) await alpaca.closeAllPositions();
  event('stop', closePositions ? 'STOP: objednávky zrušeny a pozice uzavřeny.' : 'STOP: otevřené objednávky zrušeny.');
}
export function resume() { state.halted = false; event('resume', 'Systém znovu povolen.'); }
