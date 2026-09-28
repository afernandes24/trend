// Wspólne narzędzia: API, formatowanie, DOM, toasty.

export async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.json);
    delete init.json;
  }
  const res = await fetch(`/api${path}`, init);
  let data = null;
  try { data = await res.json(); } catch { /* brak treści */ }
  if (!res.ok) {
    const msg = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('pl-PL', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export const fmtPct = (v, d = 2) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, d)}%`);
export const fmtR = (v, d = 2) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, d)}R`);
export const signCls = (v) => (v > 0 ? 'long' : v < 0 ? 'short' : 'muted');

export function fmtTime(iso, withDate = true) {
  if (!iso) return '—';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(iso);
  const opt = withDate
    ? { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' };
  return d.toLocaleString('pl-PL', opt);
}
export function fmtDate(iso) {
  if (!iso) return '—';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(iso);
  return d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
export function ago(iso) {
  if (!iso) return 'nigdy';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)} s temu`;
  if (s < 3600) return `${Math.round(s / 60)} min temu`;
  if (s < 86400) return `${Math.round(s / 3600)} h temu`;
  return `${Math.round(s / 86400)} d temu`;
}

export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function toast(text, kind = '', ms = 6000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = text;
  el.onclick = () => el.remove();
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function bandClass(band) {
  return 'band-' + String(band || 'BRAK').split(' ')[0];
}
export function scoreBarClass(s) {
  if (s === null || s === undefined) return 's-weak';
  if (s >= 85) return 's-strong';
  if (s >= 75) return 's-good';
  if (s >= 65) return '';
  return 's-weak';
}
export function scoreBar(s) {
  return `<div class="score-bar ${scoreBarClass(s)}"><i style="width:${Math.max(0, Math.min(100, s || 0))}%"></i></div>`;
}
export function trendLabel(t) {
  if (t === 1) return '<span class="trend long">▲ Wzrostowy</span>';
  if (t === -1) return '<span class="trend short">▼ Spadkowy</span>';
  if (t === 0) return '<span class="trend muted">◆ Boczny</span>';
  return '<span class="trend dim">—</span>';
}
export function mtfDots(rows) {
  return `<div class="mtf-dots">${(rows || []).map((r) => {
    const c = r.trend === 1 ? 'up' : r.trend === -1 ? 'down' : '';
    return `<span class="mtf-dot ${c}" title="${r.tf}: ${r.trend === 1 ? 'wzrost' : r.trend === -1 ? 'spadek' : 'brak trendu'}">${r.tf}</span>`;
  }).join('')}</div>`;
}
export function sigBadge(sig, lg = false) {
  const s = sig || 'WAIT';
  return `<span class="badge ${s} ${lg ? 'lg' : ''}">${s}</span>`;
}
export const isPro = () => store.get('pro', false);
export const qs = () => Object.fromEntries(new URLSearchParams(location.hash.split('?')[1] || ''));

// Zapamiętywanie drobnych preferencji widza (bez gwarancji — przeglądarka może to czyścić)
export const store = {
  get(k, d) { try { const v = localStorage.getItem('aura:' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('aura:' + k, JSON.stringify(v)); } catch { /* ignoruj */ } },
};
