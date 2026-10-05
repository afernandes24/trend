// STATYSTYKI — alerty wysłane na Telegram (ogólne) i Twoje transakcje.
import { api, esc, fmt, fmtR, signCls, store } from '../lib.js';

const pln = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, 2)} PLN`);
const pct = (v, d = 0) => (v === null || v === undefined ? '—' : `${fmt(v, d)}%`);

function kpi(label, value, sub = '', cls = '') {
  return `<div class="panel kpi"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="sub">${sub}</div></div>`;
}

function table(rows, key, unit) {
  if (!rows.length) return '';
  const max = Math.max(...rows.map((r) => Math.abs(r.total)), 1e-9);
  return `<div class="table-wrap" style="margin-top:14px"><table class="t"><thead><tr>
    <th>${key === 'symbol' ? 'Instrument' : 'Kierunek'}</th><th class="r">Liczba</th><th class="r">Na plus</th><th class="r">Wynik</th></tr></thead><tbody>
    ${rows.map((r) => `<tr>
      <td data-l="${key === 'symbol' ? 'Instrument' : 'Kierunek'}"><b class="${key === 'direction' ? (r[key] === 'LONG' ? 'long' : 'short') : ''}">${esc(r[key])}</b>
        <div class="bar-mini"><span style="width:${Math.abs(r.total) / max * 100}%;background:var(--${r.total >= 0 ? 'long' : 'short'})"></span></div></td>
      <td data-l="Liczba" class="r mono">${r.n}</td>
      <td data-l="Na plus" class="r mono">${pct(r.win_rate)}</td>
      <td data-l="Wynik" class="r mono ${signCls(r.total)}" style="font-weight:600">${unit === 'R' ? fmtR(r.total, 1) : pln(r.total)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

export async function render(root, ctx) {
  root.innerHTML = `
    <div class="row between" style="margin-bottom:16px;gap:10px;flex-wrap:wrap">
      <div class="muted" style="font-size:13px">Okres:</div>
      <div class="seg" id="st-days">${[[7, '7 dni'], [30, '30 dni'], [90, '90 dni'], [0, 'wszystko']].map(([v, l]) => `<button data-d="${v}" class="${v === store.get('stDays', 30) ? 'on' : ''}">${l}</button>`).join('')}</div>
    </div>
    <div class="stat-cols" id="st-body"><div class="panel skeleton" style="height:320px"></div><div class="panel skeleton" style="height:320px"></div></div>`;

  async function load() {
    let d;
    try { d = await api(`/stats?days=${store.get('stDays', 30)}`); } catch (e) { root.querySelector('#st-body').innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
    if (!ctx.isCurrent()) return;
    const a = d.alerts, t = d.trades;
    const res = Object.entries(a.results || {}).map(([k, v]) => `<span class="badge ${k === 'SL' ? 'SHORT' : k.startsWith('TP') ? 'LONG' : 'info'}">${esc(k === 'EXPIRED' ? 'wygasłe' : k)} × ${v}</span>`).join(' ');
    root.querySelector('#st-body').innerHTML = `
      <div class="panel panel-pad">
        <div class="section-title" style="margin-top:0">Alerty <span class="muted" style="font-weight:400">· wysłane na Telegram</span></div>
        <div class="kpis">
          ${kpi('Alerty', a.count, `zamknięte ${a.closed} · w grze ${a.open}`)}
          ${kpi('Trafność', pct(a.style_win_rate), a.closed ? `${a.closed} rozstrzygniętych` : 'brak rozstrzygniętych')}
          ${kpi('Wynik', fmtR(a.style_total_r, 1), `≈ ${a.style_pct > 0 ? '+' : ''}${fmt(a.style_pct, 1)}% konta przy ${fmt(d.risk_pct, 1)}% ryzyka`, signCls(a.style_total_r))}
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
          ${kpi('Wynik', pln(t.total_pln), t.pct_capital !== null ? `${t.pct_capital > 0 ? '+' : ''}${fmt(t.pct_capital, 1)}% kapitału (${fmt(d.capital, 0)} PLN)` : '', signCls(t.total_pln))}
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
        ${t.trades ? '' : '<div class="empty">Brak zamkniętych transakcji w tym okresie. Zapisuj pozycje w „Moje pozycje” albo odpowiadając na alert w Telegramie.</div>'}
      </div>`;
  }
  root.querySelectorAll('#st-days button').forEach((b) => {
    b.onclick = () => {
      store.set('stDays', +b.dataset.d);
      root.querySelectorAll('#st-days button').forEach((x) => x.classList.toggle('on', x === b));
      load();
    };
  });
  await load();
  const t = setInterval(load, 60000);
  return () => clearInterval(t);
}
