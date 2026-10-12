import { config } from './config.js';

const headers = () => ({
  'APCA-API-KEY-ID': config.key,
  'APCA-API-SECRET-KEY': config.secret,
  'Content-Type': 'application/json',
});

async function request(base, pathname, options = {}) {
  const response = await fetch(`${base}${pathname}`, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(body?.message || `${response.status} ${response.statusText}`);
  return body;
}

export const getAccount = () => request(config.paperBase, '/v2/account');
export const getClock = () => request(config.paperBase, '/v2/clock');
export const getPositions = () => request(config.paperBase, '/v2/positions');
export const getOrders = () => request(config.paperBase, '/v2/orders?status=open&nested=true&limit=100');

export async function getBars(symbol, limit = 100) {
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  const query = new URLSearchParams({ timeframe: '1Min', start: start.toISOString(), end: end.toISOString(), limit: String(limit), feed: config.feed });
  const data = await request(config.dataBase, `/v2/stocks/${symbol}/bars?${query}`);
  return data.bars || [];
}

export async function submitBracket(plan) {
  const body = {
    symbol: plan.symbol,
    qty: String(plan.qty),
    side: plan.side,
    type: 'market',
    time_in_force: 'day',
    order_class: 'bracket',
    take_profit: { limit_price: plan.target.toFixed(2) },
    stop_loss: { stop_price: plan.stop.toFixed(2) },
    client_order_id: `scalp-${Date.now()}`,
  };
  return request(config.paperBase, '/v2/orders', { method: 'POST', body: JSON.stringify(body) });
}

export const cancelAllOrders = () => request(config.paperBase, '/v2/orders', { method: 'DELETE' });
export const closeAllPositions = () => request(config.paperBase, '/v2/positions?cancel_orders=true', { method: 'DELETE' });

export function connectMarketStream({ symbols, onBar, onQuote, onStatus }) {
  let socket;
  let reconnect;
  let stopped = false;
  const open = () => {
    onStatus('Připojuji živá data…');
    socket = new WebSocket(`wss://stream.data.alpaca.markets/v2/${config.feed}`);
    socket.addEventListener('open', () => socket.send(JSON.stringify({ action: 'auth', key: config.key, secret: config.secret })));
    socket.addEventListener('message', event => {
      for (const message of JSON.parse(event.data)) {
        if (message.T === 'success' && message.msg === 'authenticated') {
          socket.send(JSON.stringify({ action: 'subscribe', bars: symbols, quotes: symbols }));
          onStatus(`Živá data: ${config.feed.toUpperCase()}`);
        } else if (message.T === 'b') onBar(message);
        else if (message.T === 'q') onQuote(message);
        else if (message.T === 'error') onStatus(`Chyba streamu: ${message.msg}`);
      }
    });
    socket.addEventListener('error', () => onStatus('Chyba spojení s datovým streamem.'));
    socket.addEventListener('close', () => {
      if (!stopped) reconnect = setTimeout(open, 3000);
    });
  };
  open();
  return () => { stopped = true; clearTimeout(reconnect); socket?.close(); };
}
