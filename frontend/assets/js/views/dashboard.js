// DASHBOARD — Portfolio / Market overview + kafelki 10 instrumentów.
import { api, esc, fmt, fmtPct, fmtTime, mtfDots, scoreBar, sigBadge, signCls, trendLabel, bandClass } from '../lib.js';

export async function render(root, ctx) {
  root.innerHTML = `
    <div class="kpis" id="kpis">${'<div class="panel kpi skeleton" style="height:84px"></div>'.repeat(4)}</div>
    <h2 class="section-title">Instrumenty XTB</h2>
    <div class="tiles" id="tiles">${'<div class="panel skeleton" style="height:210px"></div>'.repeat(10)}</div>
    <p class="disclaimer">Score 0–100 opisuje, ile warunków strategii jest spełnionych — nie jest prawdopodobieństwem zysku.
      Sygnał LONG/SHORT wymaga score ≥ progu, spełnionych warunków trendu na interwale setupu (15M) i zgodności 4H + 1H.
      Ceny pochodzą ze źródła danych ustawionego w konfiguracji (domyślnie proxy futures/indeksów) i mogą różnić się od notowań CFD w XTB.</p>`;

  async function load() {
    let data;
    try {
      data = await api('/overview');
    } catch (e) {
      root.querySelector('#tiles').innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
      return;
    }
    if (!ctx.isCurrent()) return;
    const items = data.items;
    const withData = items.filter((t) => t.price !== null && t.price !== undefined);
    const longs = items.filter((t) => t.signal === 'LONG').length;
    const shorts = items.filter((t) => t.signal === 'SHORT').length;
    const watch = items.filter((t) => t.signal === 'WAIT' && t.score >= 65).length;
    const aligned = items.filter((t) => t.higher_tf_aligned).length;
    root.querySelector('#kpis').innerHTML = `
      <div class="panel kpi"><div class="label">Setupy LONG</div><div class="value long">${longs}</div><div class="sub">score ≥ ${items[0]?.min_score ?? 75}, MTF zgodne</div></div>
      <div class="panel kpi"><div class="label">Setupy SHORT</div><div class="value short">${shorts}</div><div class="sub">score ≥ ${items[0]?.min_score ?? 75}, MTF zgodne</div></div>
      <div class="panel kpi"><div class="label">Obserwuj</div><div class="value info">${watch}</div><div class="sub">WAIT ze score ≥ 65</div></div>
      <div class="panel kpi"><div class="label">Zgodność 4H/1H</div><div class="value">${aligned}<span class="muted" style="font-size:14px">/${items.length}</span></div><div class="sub">Dane: ${withData.length}/${items.length} instrumentów</div></div>`;

    const tiles = root.querySelector('#tiles');
    if (!withData.length) {
      tiles.innerHTML = `<div class="panel panel-pad" style="grid-column:1/-1">
        <div class="errbox" style="margin-bottom:12px">Brak danych rynkowych. Aplikacja nie pokazuje sztucznych cen.</div>
        <div class="notebox">Kliknij <b>↻ Odśwież dane</b> u góry, sprawdź połączenie z internetem albo zaimportuj pliki CSV w <a href="#/settings">Ustawieniach</a>.
        ${items.find((t) => t.error) ? `<br><br>Ostatni błąd: <span class="mono">${esc(items.find((t) => t.error).error)}</span>` : ''}</div></div>`;
      return;
    }
    tiles.innerHTML = items.map(tile).join('');
    tiles.querySelectorAll('.tile').forEach((el) => {
      el.onclick = () => { location.hash = `#/analysis/${encodeURIComponent(el.dataset.sym)}`; };
    });
  }

  await load();
  const timer = setInterval(load, 30000);
  return () => clearInterval(timer);
}

function tile(t) {
  const d = t.digits ?? 2;
  const hasData = t.price !== null && t.price !== undefined;
  return `
  <div class="panel tile fade-in sig-${t.signal}" data-sym="${esc(t.symbol)}">
    <div class="tile-top">
      <div><div class="sym">${esc(t.symbol)}</div><div class="sym-name">${esc(t.name || '')}</div></div>
      ${sigBadge(t.signal)}
    </div>
    <div class="price-row">
      <span class="price">${hasData ? fmt(t.price, d) : '—'}</span>
      <span class="chg ${signCls(t.change_pct)}">${fmtPct(t.change_pct)}</span>
    </div>
    <div class="row between" style="margin-bottom:10px">
      <span class="muted" style="font-size:11.5px">Trend 4H</span>${trendLabel(t.trend)}
    </div>
    <div class="stats">
      <div class="stat"><div class="k">RSI</div><div class="v">${fmt(t.rsi, 1)}</div></div>
      <div class="stat"><div class="k">ADX</div><div class="v">${fmt(t.adx, 1)}</div></div>
      <div class="stat"><div class="k">SCORE</div><div class="v">${t.score ?? '—'}</div></div>
    </div>
    ${scoreBar(t.score)}
    <div class="tile-foot">
      ${mtfDots(t.mtf)}
      <span class="band ${bandClass(t.band)}">${esc(t.band || '')}</span>
    </div>
    ${t.error && !hasData ? `<div class="muted" style="font-size:11px;margin-top:8px">${esc(t.error)}</div>` : ''}
    ${t.price_time ? `<div class="dim" style="font-size:11px;margin-top:10px">${fmtTime(t.price_time)}${t.direction && t.signal === 'WAIT' ? ` · kandydat ${t.direction}` : ''}</div>` : ''}
  </div>`;
}
