// STATYSTYKI — tydzień / miesiąc / całość, % zysku na instrumentach, skuteczność wg score (co 5 pkt).
import { api, esc, fmt, fmtR, signCls, store } from '../lib.js';

const pln = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, 2)} PLN`);
const pct = (v, d = 0) => (v === null || v === undefined ? '—' : `${fmt(v, d)}%`);
const spct = (v, d = 1) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, d)}%`);
const PERIODS = [['week', 'Ten tydzień'], ['month', 'Ten miesiąc'], ['all', 'Całość']];

function kpi(label, value, sub = '', cls = '') {
  return `<div class="panel kpi"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="sub">${sub}</div></div>`;
}

const bar = (v, max, color) => `<div class="bar-mini"><span style="width:${Math.min(100, Math.abs(v) / (max || 1) * 100)}%;background:var(--${color})"></span></div>`;

// tabela per instrument / kierunek: liczba, na plus, wynik w % (+ R albo PLN)
function table(rows, key, unit) {
  if (!rows.length) return '';
  const max = Math.max(...rows.map((r) => Math.abs(r.pct ?? r.total)), 1e-9);
  const name = key === 'symbol' ? 'Instrument' : 'Kierunek';
  return `<div class="table-wrap" style="margin-top:14px"><table class="t"><thead><tr>
    <th>${name}</th><th class="r">Liczba</th><th class="r">Na plus</th><th class="r">${unit === 'R' ? 'R' : 'PLN'}</th><th class="r">Zysk %</th></tr></thead><tbody>
    ${rows.map((r) => `<tr>
      <td><b class="${key === 'direction' ? (r[key] === 'LONG' ? 'long' : 'short') : ''}">${esc(r[key])}</b>${bar(r.pct ?? r.total, max, (r.pct ?? r.total) >= 0 ? 'long' : 'short')}</td>
      <td class="r mono">${r.n}</td>
      <td class="r mono">${pct(r.win_rate)}</td>
      <td class="r mono muted">${unit === 'R' ? fmtR(r.total, 1) : pln(r.total)}</td>
      <td class="r mono ${signCls(r.pct)}" style="font-weight:600">${spct(r.pct)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

// skuteczność wg score co 5 punktów
function scoreTable(alerts, all, unit) {
  const keys = [...new Set([...(alerts || []).map((b) => b.from), ...(all || []).map((b) => b.from)])].sort((a, b) => a - b);
  if (!keys.length) return '<div class="empty">Brak rozstrzygniętych sygnałów w tym okresie.</div>';
  const A = Object.fromEntries((alerts || []).map((b) => [b.from, b]));
  const S = Object.fromEntries((all || []).map((b) => [b.from, b]));
  const label = (k) => (k === 95 ? '95–100' : `${k}–${k + 4}`);
  return `<div class="table-wrap"><table class="t"><thead><tr><th>Score</th>
      <th class="r">${unit === 'R' ? 'Alerty' : 'Transakcje'}</th><th class="r">Trafność</th><th class="r">Śr. ${unit === 'R' ? 'R' : 'PLN'}</th><th class="r">Zysk %</th>
      ${all ? '<th class="r">Wszystkie sygnały</th><th class="r">Trafność</th>' : ''}</tr></thead><tbody>
    ${keys.map((k) => {
      const a = A[k], s = S[k];
      return `<tr><td class="mono"><b>${label(k)}</b>${a ? bar(a.win_rate, 100, a.win_rate >= 50 ? 'long' : 'short') : ''}</td>
        <td class="r mono">${a ? a.n : '—'}</td>
        <td class="r mono" style="font-weight:600">${a ? pct(a.win_rate) : '—'}</td>
        <td class="r mono ${signCls(a?.avg)}">${a ? (unit === 'R' ? fmtR(a.avg, 2) : pln(a.avg)) : '—'}</td>
        <td class="r mono ${signCls(a?.pct)}" style="font-weight:600">${a ? spct(a.pct) : '—'}</td>
        ${all ? `<td class="r mono muted">${s ? s.n : '—'}</td><td class="r mono muted">${s ? pct(s.win_rate) : '—'}</td>` : ''}</tr>`;
    }).join('')}</tbody></table></div>`;
}

export async function render(root, ctx) {
  const cur = () => store.get('stPeriod', 'month');
  root.innerHTML = `
    <div class="panel" id="st-sum"><div class="skeleton" style="height:150px;margin:16px"></div></div>
    <div class="row between" style="margin:24px 0 14px;gap:10px;flex-wrap:wrap">
      <h2 class="section-title" style="margin:0">Szczegóły</h2>
      <div class="seg" id="st-per">${PERIODS.map(([v, l]) => `<button data-p="${v}" class="${v === cur() ? 'on' : ''}">${l}</button>`).join('')}</div>
    </div>
    <div class="panel panel-pad" id="st-score" style="margin-bottom:16px"></div>
    <div class="stat-cols" id="st-body"><div class="panel skeleton" style="height:320px"></div><div class="panel skeleton" style="height:320px"></div></div>`;

  async function load() {
    let d;
    try { d = await api(`/stats?period=${cur()}`); } catch (e) { root.querySelector('#st-body').innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
    if (!ctx.isCurrent()) return;
    const a = d.alerts, t = d.trades;

    // --- podsumowanie: tydzień / miesiąc / całość
    root.querySelector('#st-sum').innerHTML = `<div class="table-wrap"><table class="t"><thead><tr>
        <th>Okres</th><th class="r">Alerty</th><th class="r">Trafność</th><th class="r">Zysk z alertów</th>
        <th class="r">Moje transakcje</th><th class="r">Na plus</th><th class="r">Wynik</th><th class="r">Zysk %</th></tr></thead><tbody>
      ${d.summary.map((s) => `<tr>
        <td><b>${esc(s.label)}</b></td>
        <td class="r mono">${s.alerts}</td>
        <td class="r mono">${pct(s.alerts_win_rate)}</td>
        <td class="r mono ${signCls(s.alerts_pct)}" style="font-weight:600">${s.alerts_closed ? spct(s.alerts_pct) : '—'}</td>
        <td class="r mono">${s.trades}</td>
        <td class="r mono">${pct(s.trades_win_rate)}</td>
        <td class="r mono ${signCls(s.trades_pln)}">${s.trades ? pln(s.trades_pln) : '—'}</td>
        <td class="r mono ${signCls(s.trades_pct)}" style="font-weight:600">${s.trades ? spct(s.trades_pct) : '—'}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="disclaimer" style="margin:0;padding:10px 16px 14px">Zysk z alertów = teoretyczny % konta przy ${fmt(d.risk_pct, 1)}% ryzyka na alert. Moje transakcje = % kapitału ${fmt(d.capital, 0)} PLN. Tydzień liczony od poniedziałku, miesiąc od 1. dnia.</p>`;

    // --- skuteczność wg score
    root.querySelector('#st-score').innerHTML = `
      <div class="section-title" style="margin-top:0">Czy wyższy score = lepszy wynik? <span class="muted" style="font-weight:400">· ${esc(d.label.toLowerCase())}, co 5 punktów</span></div>
      ${scoreTable(a.by_score, d.signals_by_score, 'R')}
      ${t.by_score && t.by_score.length ? `<div class="flabel" style="margin:18px 0 6px">Twoje transakcje wg score alertu</div>${scoreTable(t.by_score, null, 'PLN')}` : ''}
      <p class="disclaimer" style="margin-top:12px">„Wszystkie sygnały” to każdy setup wykryty przez skaner, także te, które nie poszły na Telegram (cooldown, filtry). Mała liczba w przedziale (np. poniżej 10) nie wystarcza do wniosków.</p>`;

    const res = Object.entries(a.results || {}).map(([k, v]) => `<span class="badge ${k === 'SL' ? 'SHORT' : k.startsWith('TP') ? 'LONG' : 'info'}">${esc(k === 'EXPIRED' ? 'wygasłe' : k)} × ${v}</span>`).join(' ');
    root.querySelector('#st-body').innerHTML = `
      <div class="panel panel-pad">
        <div class="section-title" style="margin-top:0">Alerty <span class="muted" style="font-weight:400">· wysłane na Telegram</span></div>
        <div class="kpis">
          ${kpi('Alerty', a.count, `zamknięte ${a.closed} · w grze ${a.open}`)}
          ${kpi('Trafność', pct(a.style_win_rate), a.closed ? `${a.closed} rozstrzygniętych` : 'brak rozstrzygniętych')}
          ${kpi('Zysk', spct(a.style_pct), `${fmtR(a.style_total_r, 1)} przy ${fmt(d.risk_pct, 1)}% ryzyka`, signCls(a.style_pct))}
          ${kpi('Wszedłeś', `${a.taken}/${a.count}`, a.profit_factor !== null ? `profit factor ${fmt(a.profit_factor, 2)}` : '')}
        </div>
        ${res ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:14px">${res}</div>` : ''}
        ${table(a.by_symbol, 'symbol', 'R')}
        ${table(a.by_direction, 'direction', 'R')}
        <p class="disclaimer" style="margin-top:14px">Liczone tak, jak grasz: TP na TP2 (${fmt(d.tp_r[1], 1)}R), po dojściu do TP1 SL przesunięty na wejście (TP1 = 0R), SL = −1R. Ceny ze źródła danych, bez spreadu — to wynik teoretyczny.</p>
      </div>
      <div class="panel panel-pad">
        <div class="section-title" style="margin-top:0">Moje transakcje <span class="muted" style="font-weight:400">· z „Moje pozycje”</span></div>
        <div class="kpis">
          ${kpi('Transakcje', t.trades, `${t.wins} na plus · ${t.losses} na minus`)}
          ${kpi('Na plus', pct(t.win_rate), t.avg_r !== null ? `średnio ${fmtR(t.avg_r, 2)}` : '')}
          ${kpi('Zysk', spct(t.pct_capital), pln(t.total_pln), signCls(t.total_pln))}
          ${kpi('Profit factor', t.profit_factor !== null ? fmt(t.profit_factor, 2) : '—', 'zyski ÷ straty')}
        </div>
        <div class="levels" style="margin-top:14px">
          <span class="lk">Średni zysk</span><span class="lv long">${pln(t.avg_win_pln)}</span>
          <span class="lk">Średnia strata</span><span class="lv short">${pln(t.avg_loss_pln)}</span>
          <span class="lk">Najlepsza</span><span class="lv">${pln(t.best_pln)}</span>
          <span class="lk">Najgorsza</span><span class="lv">${pln(t.worst_pln)}</span>
          <span class="lk">Z alertów</span><span class="lv">${t.from_alerts}/${t.trades}</span>
        </div>
        ${table(t.by_symbol, 'symbol', 'PLN')}
        ${table(t.by_direction, 'direction', 'PLN')}
        ${t.trades ? '' : '<div class="empty">Brak zamkniętych transakcji w tym okresie.</div>'}
      </div>`;
  }
  root.querySelectorAll('#st-per button').forEach((b) => {
    b.onclick = () => {
      store.set('stPeriod', b.dataset.p);
      root.querySelectorAll('#st-per button').forEach((x) => x.classList.toggle('on', x === b));
      load();
    };
  });
  await load();
  const timer = setInterval(load, 60000);
  return () => clearInterval(timer);
}
