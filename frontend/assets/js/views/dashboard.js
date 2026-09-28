// RYNEK — przegląd 10 instrumentów XTB (tryb prosty) + szczegóły (tryb zaawansowany).
import { api, esc, fmt, fmtPct, fmtTime, isPro, mtfDots, scoreBar, sigBadge, signCls, trendLabel, bandClass } from '../lib.js';

const pln = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, 2)} PLN`);

export async function render(root, ctx) {
  const pro = isPro();
  root.innerHTML = `
    <div class="kpis" id="kpis">${'<div class="panel kpi skeleton" style="height:84px"></div>'.repeat(4)}</div>
    <h2 class="section-title">Instrumenty XTB</h2>
    <div class="tiles" id="tiles">${'<div class="panel skeleton" style="height:150px"></div>'.repeat(10)}</div>
    <p class="disclaimer">Ceny z Yahoo (futures / indeksy) — w XTB mogą się różnić. Score nie jest prawdopodobieństwem zysku.</p>`;

  async function load() {
    let data, pos = null;
    try {
      [data, pos] = await Promise.all([api('/overview'), api('/positions').catch(() => null)]);
    } catch (e) {
      root.querySelector('#tiles').innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
      return;
    }
    if (!ctx.isCurrent()) return;
    const items = data.items;
    const withData = items.filter((t) => t.price !== null && t.price !== undefined);
    const active = items.filter((t) => t.signal !== 'WAIT');
    const st = pos?.stats;
    root.querySelector('#kpis').innerHTML = `
      <div class="panel kpi"><div class="label">Sygnały teraz</div><div class="value">${active.length}</div>
        <div class="sub">${active.length ? active.map((t) => `<span class="${t.signal === 'LONG' ? 'long' : 'short'}">${esc(t.symbol)}</span>`).join(', ') : 'brak — czekamy na setup'}</div></div>
      <div class="panel kpi"><div class="label">Otwarte pozycje</div><div class="value ${signCls(st?.open_est_pln)}">${st ? pln(st.open_est_pln) : '—'}</div><div class="sub">${st ? `${st.open_count} poz.` : ''} · <a href="#/positions">szczegóły</a></div></div>
      <div class="panel kpi"><div class="label">Wynik dziś</div><div class="value ${signCls(st?.today_pln)}">${st ? pln(st.today_pln) : '—'}</div><div class="sub">zamknięte pozycje</div></div>
      <div class="panel kpi"><div class="label">Wynik w tym miesiącu</div><div class="value ${signCls(st?.month_pln)}">${st ? pln(st.month_pln) : '—'}</div><div class="sub">${st && st.trades ? `${st.trades} transakcji łącznie` : 'zapisuj pozycje w „Moje pozycje”'}</div></div>`;

    const tiles = root.querySelector('#tiles');
    if (!withData.length) {
      tiles.innerHTML = `<div class="panel panel-pad" style="grid-column:1/-1">
        <div class="errbox" style="margin-bottom:12px">Brak danych rynkowych. Aplikacja nie pokazuje sztucznych cen.</div>
        <div class="notebox">Kliknij <b>↻ Odśwież</b> u góry i odczekaj minutę.
        ${items.find((t) => t.error) ? `<br><br>Ostatni błąd: <span class="mono">${esc(items.find((t) => t.error).error)}</span>` : ''}</div></div>`;
      return;
    }
    const ordered = pro ? items : [...active, ...items.filter((t) => t.signal === 'WAIT')];
    tiles.innerHTML = ordered.map((t) => (pro ? tilePro(t) : tileSimple(t))).join('');
    tiles.querySelectorAll('.tile').forEach((el) => {
      el.onclick = (e) => {
        if (e.target.closest('[data-enter]')) return;
        location.hash = `#/analysis/${encodeURIComponent(el.dataset.sym)}`;
      };
    });
  }

  await load();
  const timer = setInterval(load, 30000);
  return () => clearInterval(timer);
}

function enterLink(t) {
  if (t.signal === 'WAIT' || !t.sl_d) return '';
  const q = new URLSearchParams({ symbol: t.symbol, dir: t.signal, sl_d: t.sl_d, tp_d: t.tp_d });
  return `<a class="btn sm primary" data-enter href="#/positions?${q}" style="margin-top:14px;width:100%">Wszedłem w pozycję</a>`;
}

function tileSimple(t) {
  const d = t.digits ?? 2;
  return `
  <div class="panel tile fade-in sig-${t.signal}" data-sym="${esc(t.symbol)}">
    <div class="tile-top">
      <div><div class="sym">${esc(t.symbol)}</div><div class="sym-name">${esc(t.name || '')}</div></div>
      ${sigBadge(t.signal)}
    </div>
    <div class="price-row" style="margin-bottom:6px">
      <span class="price">${t.price !== null && t.price !== undefined ? fmt(t.price, d) : '—'}</span>
      <span class="chg ${signCls(t.change_pct)}">${fmtPct(t.change_pct)}</span>
    </div>
    <div class="row between" style="font-size:12px"><span class="muted">Score</span><span class="mono">${t.score ?? '—'}</span></div>
    ${scoreBar(t.score)}
    ${enterLink(t)}
  </div>`;
}

function tilePro(t) {
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
    ${t.price_time ? `<div class="dim" style="font-size:11px;margin-top:10px">${fmtTime(t.price_time)}${t.direction && t.signal === 'WAIT' ? ` · kandydat ${t.direction}` : ''}</div>` : ''}
    ${enterLink(t)}
  </div>`;
}
