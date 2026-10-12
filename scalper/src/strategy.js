const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;

export function ema(values, period) {
  if (values.length < period) return null;
  const k = 2 / (period + 1);
  let out = mean(values.slice(0, period));
  for (const value of values.slice(period)) out = value * k + out * (1 - k);
  return out;
}

export function atr(bars, period = 14) {
  if (bars.length < period + 1) return null;
  const trs = bars.slice(1).map((bar, i) => Math.max(
    bar.h - bar.l,
    Math.abs(bar.h - bars[i].c),
    Math.abs(bar.l - bars[i].c),
  ));
  return mean(trs.slice(-period));
}

export function vwap(bars) {
  const valid = bars.filter(b => b.v > 0);
  const volume = valid.reduce((sum, b) => sum + b.v, 0);
  if (!volume) return null;
  return valid.reduce((sum, b) => sum + ((b.h + b.l + b.c) / 3) * b.v, 0) / volume;
}

export function evaluateSignal(bars, quote, settings = {}) {
  if (bars.length < 25 || !quote?.bid || !quote?.ask) return { side: 'WAIT', reason: 'Čekám na alespoň 25 svíček a platnou kotaci.' };
  const closes = bars.map(b => b.c);
  const fast = ema(closes, 9);
  const slow = ema(closes, 20);
  const sessionVwap = vwap(bars);
  const currentAtr = atr(bars);
  const last = bars.at(-1);
  const previous = bars.at(-2);
  const spreadBps = ((quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2)) * 10000;
  const averageVolume = mean(bars.slice(-21, -1).map(b => b.v));
  const relativeVolume = averageVolume ? last.v / averageVolume : 0;
  const maxSpreadBps = settings.maxSpreadBps ?? 8;

  const base = { price: last.c, ema9: fast, ema20: slow, vwap: sessionVwap, atr: currentAtr, spreadBps, relativeVolume };
  if (spreadBps > maxSpreadBps) return { ...base, side: 'WAIT', reason: `Spread ${spreadBps.toFixed(1)} bps je příliš široký.` };
  if (!currentAtr || currentAtr <= 0) return { ...base, side: 'WAIT', reason: 'ATR zatím není dostupné.' };

  const long = last.c > sessionVwap && fast > slow && last.c > previous.h && relativeVolume >= 1.15;
  const short = last.c < sessionVwap && fast < slow && last.c < previous.l && relativeVolume >= 1.15;
  if (long) return { ...base, side: 'BUY', reason: 'Průraz nad předchozí high, VWAP a EMA trend potvrzeny.' };
  if (short) return { ...base, side: 'SELL', reason: 'Průraz pod předchozí low, VWAP a EMA trend potvrzeny.' };
  return { ...base, side: 'WAIT', reason: 'Podmínky pro vstup nejsou současně splněny.' };
}

export function buildTradePlan({ signal, equity, buyingPower, settings }) {
  if (!['BUY', 'SELL'].includes(signal.side)) return null;
  const riskCash = equity * (settings.riskPerTradePct / 100);
  const stopDistance = Math.max(signal.atr * settings.stopAtrMultiplier, signal.price * 0.0005);
  const qtyByRisk = Math.floor(riskCash / stopDistance);
  const qtyByPower = Math.floor((buyingPower * 0.2) / signal.price);
  const qty = Math.max(0, Math.min(qtyByRisk, qtyByPower));
  if (!qty) return null;
  const direction = signal.side === 'BUY' ? 1 : -1;
  return {
    symbol: signal.symbol,
    side: signal.side.toLowerCase(),
    qty,
    entry: signal.price,
    stop: signal.price - direction * stopDistance,
    target: signal.price + direction * stopDistance * settings.targetRMultiplier,
    riskCash: qty * stopDistance,
  };
}
