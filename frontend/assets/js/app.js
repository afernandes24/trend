// TREND FOLLOWING — router, nawigacja, status i powiadomienia w aplikacji.
import { api, ago, esc, store, toast } from './lib.js';
import * as dashboard from './views/dashboard.js';
import * as scanner from './views/scanner.js';
import * as analysis from './views/analysis.js';
import * as backtest from './views/backtest.js';
import * as history from './views/history.js';
import * as alerts from './views/alerts.js';
import * as risk from './views/risk.js';
import * as settings from './views/settings.js';

const I = {
  grid: '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
  chart: '<path d="M7 4v16M17 4v16"/><rect x="5" y="8" width="4" height="7" rx="1"/><rect x="15" y="6" width="4" height="9" rx="1"/>',
  flask: '<path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4A1.5 1.5 0 0 0 20 19l-6-10V3"/><path d="M7 15h10"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};
const svg = (p) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;

const ROUTES = [
  { id: 'dashboard', label: 'Dashboard', short: 'Rynek', icon: I.grid, mod: dashboard, kicker: 'Portfolio / Market overview' },
  { id: 'scanner', label: 'Market Scanner', short: 'Skaner', icon: I.radar, mod: scanner, kicker: 'Skanowanie 10 instrumentów' },
  { id: 'analysis', label: 'Wykres i analiza', short: 'Wykres', icon: I.chart, mod: analysis, kicker: 'Analiza instrumentu' },
  { id: 'backtest', label: 'Backtesting', short: 'Backtest', icon: I.flask, mod: backtest, kicker: 'Test historyczny strategii' },
  { id: 'history', label: 'Historia sygnałów', short: 'Historia', icon: I.list, mod: history, kicker: 'Zarejestrowane setupy' },
  { id: 'alerts', label: 'Alerty', short: 'Alerty', icon: I.bell, mod: alerts, kicker: 'Powiadomienia Telegram / e-mail / SMS' },
  { id: 'risk', label: 'Kalkulator ryzyka', short: 'Ryzyko', icon: I.shield, mod: risk, kicker: 'Wielkość pozycji' },
  { id: 'settings', label: 'Ustawienia', short: 'Ustaw.', icon: I.cog, mod: settings, kicker: 'Strategia i dane' },
];

let cleanup = null;
let renderToken = 0;

function buildNav() {
  const html = ROUTES.map((r) => `<a href="#/${r.id}" data-id="${r.id}">${svg(r.icon)}<span>${r.label}</span></a>`).join('');
  document.getElementById('nav').innerHTML = html;
  document.getElementById('mobile-nav').innerHTML = ROUTES.map((r) => `<a href="#/${r.id}" data-id="${r.id}">${svg(r.icon)}<span>${r.short}</span></a>`).join('');
}

async function route() {
  const [path] = location.hash.replace(/^#\/?/, '').split('?');
  const [id, ...rest] = path.split('/');
  const r = ROUTES.find((x) => x.id === id) || ROUTES[0];
  document.querySelectorAll('#nav a, #mobile-nav a').forEach((a) => a.classList.toggle('active', a.dataset.id === r.id));
  document.getElementById('page-kicker').textContent = r.kicker;
  document.getElementById('page-title').textContent = r.label;
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* */ } }
  cleanup = null;
  const view = document.getElementById('view');
  view.innerHTML = '';
  const token = ++renderToken;
  try {
    const c = await r.mod.render(view, { args: rest, setTitle: (t) => { document.getElementById('page-title').textContent = t; }, isCurrent: () => token === renderToken });
    if (token === renderToken) cleanup = c; else if (typeof c === 'function') c();
  } catch (e) {
    view.innerHTML = `<div class="errbox">Błąd widoku: ${esc(e.message)}</div>`;
  }
  window.scrollTo({ top: 0 });
}

// ------------------------------------------------------------- status
async function refreshStatus() {
  const row = document.getElementById('status-row');
  try {
    const hs = await api('/health');
    const sch = hs.scheduler || {};
    const busy = sch.scan_in_progress;
    const err = sch.last_error;
    row.innerHTML = `
      <span class="chip" title="${esc(err || '')}"><span class="dot ${busy ? 'busy' : err ? 'err' : sch.last_cycle ? 'ok' : ''}"></span>
        ${busy ? 'Skanowanie…' : err ? 'Błąd pobierania danych' : `Skan: ${ago(hs.last_scan)}`}</span>
      <span class="chip" title="Źródło danych rynkowych">Dane: ${esc(hs.provider)}</span>
      <button class="btn sm" id="btn-refresh-all" ${busy ? 'disabled' : ''}>↻ Odśwież dane</button>`;
    row.querySelector('#btn-refresh-all').onclick = async (ev) => {
      ev.target.disabled = true;
      ev.target.innerHTML = '<span class="spinner"></span> Pobieranie…';
      try {
        const r = await api('/data/refresh', { method: 'POST' });
        toast(r.ok ? 'Dane odświeżone, skan zakończony.' : `Błąd: ${r.error}`, r.ok ? '' : 'error');
      } catch (e) { toast(e.message, 'error'); }
      refreshStatus();
      route();
    };
  } catch (e) {
    row.innerHTML = `<span class="chip"><span class="dot err"></span>Brak połączenia z backendem</span>`;
  }
}

// ------------------------------------------------------------- alerty w aplikacji
let lastAlertId = null;
async function pollAlerts() {
  try {
    const { items } = await api(`/alerts/log?since_id=${lastAlertId ?? 0}&limit=50`);
    if (lastAlertId === null) {
      lastAlertId = items.length ? items[0].id : 0;
      return;
    }
    const fresh = items.filter((a) => a.channel === 'app' && a.status === 'sent').reverse();
    if (items.length) lastAlertId = Math.max(lastAlertId, ...items.map((a) => a.id));
    for (const a of fresh) {
      toast(a.message.split('\n').slice(0, 12).join('\n'), 'alert', 15000);
      if (store.get('browserNotify', false) && 'Notification' in window && Notification.permission === 'granted') {
        new Notification(`🚨 ${a.symbol} ${a.direction}`, { body: a.message.split('\n').slice(4, 11).join('\n') });
      }
    }
  } catch { /* backend chwilowo niedostępny */ }
}

buildNav();
window.addEventListener('hashchange', route);
if (!location.hash) location.hash = '#/dashboard';
route();
refreshStatus();
pollAlerts();
setInterval(refreshStatus, 20000);
setInterval(pollAlerts, 20000);
