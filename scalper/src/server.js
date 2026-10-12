import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { config, hasCredentials } from './config.js';
import * as engine from './engine.js';

const root = path.resolve('public');
const json = (res, status, data) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); };
const body = req => new Promise((resolve, reject) => { let raw = ''; req.on('data', c => raw += c); req.on('end', () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch (e) { reject(e); } }); });

const server = http.createServer(async (req, res) => {
  try {
    if (req.url === '/api/state') return json(res, 200, engine.getState());
    if (req.url === '/api/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
      const unsubscribe = engine.subscribe(data => res.write(`data: ${JSON.stringify(data)}\n\n`));
      req.on('close', unsubscribe); return;
    }
    if (req.method === 'POST' && req.url === '/api/execute') {
      const { symbol, confirm } = await body(req);
      if (confirm !== 'PAPER') return json(res, 400, { error: 'Chybí potvrzení PAPER.' });
      return json(res, 200, await engine.execute(String(symbol).toUpperCase()));
    }
    if (req.method === 'POST' && req.url === '/api/stop') {
      const { closePositions = false } = await body(req); await engine.emergencyStop(Boolean(closePositions)); return json(res, 200, { ok: true });
    }
    if (req.method === 'POST' && req.url === '/api/resume') { engine.resume(); return json(res, 200, { ok: true }); }
    const requestPath = req.url === '/' ? '/index.html' : req.url.split('?')[0];
    const file = path.resolve(root, `.${requestPath}`);
    if (!file.startsWith(root) || !fs.existsSync(file)) return json(res, 404, { error: 'Nenalezeno' });
    const ext = path.extname(file); const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' };
    res.writeHead(200, { 'Content-Type': `${types[ext] || 'application/octet-stream'}; charset=utf-8` }); fs.createReadStream(file).pipe(res);
  } catch (error) { json(res, 500, { error: error.message }); }
});

server.listen(config.port, '127.0.0.1', () => {
  console.log(`Alpaca Paper Scalper: http://127.0.0.1:${config.port}`);
  if (!hasCredentials()) console.log('Doplňte paper API klíče do .env.');
  else engine.start();
});
