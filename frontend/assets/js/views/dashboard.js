// RYNEK — przegląd 10 instrumentów XTB (tryb prosty) + szczegóły (tryb zaawansowany).
import { api, esc, fmt, fmtPct, fmtTime, isPro, mtfDots, scoreBar, sigBadge, signCls, toast, trendLabel, bandClass } from '../lib.js';

const pln = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmt(v, 2)} PLN`);

export async function render(root, ctx) {
  const pro = isPro();
  root.innerHTML = `
    <div class="kpis" id="kpis">${'<div class="panel kpi skeleton" style="height:84px"></div>'.repeat(4)}</div>
    <div class="row between" style="margin:32px 0 14px">
      <h2 class="section-title" style="margin:0">Instrumenty XTB</h2>
      <div class="row" style="gap:8px"><span class="muted" id="corr-info" style="font-size:12px"></span><button class="btn sm" id="btn-corr">Korekta cen do XTB</button></div>
    </div>
    <div class="tiles" id="tiles">${'<div class="panel skeleton" style="height:150px"></div>'.repeat(10)}</div>
    <p class="disclaimer">Ceny ze źródła danych mogą różnić się od XTB — w razie potrzeby ustaw „Korektę cen do XTB”. Score nie jest prawdopodobieństwem zysku.</p>
    <div id="corr-modal"></div>`;
  root.querySelector('#btn-corr').onclick = () => openCorrection(root, () => { load(); corrInfo(); });

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

  async function corrInfo() {
    try {
      const { items } = await api('/prices/offsets');
      const set = items.filter((i) => i.offset !== null);
      const old = set.filter((i) => Date.now() / 1000 - i.calibrated_at > 7 * 86400);
      root.querySelector('#corr-info').innerHTML = !set.length ? '<span class="warn">ceny bez korekty</span>'
        : old.length ? `<span class="warn">odśwież korektę: ${old.map((i) => esc(i.symbol)).join(', ')}</span>`
          : `korekta: ${set.length}/10`;
    } catch { /* */ }
  }
  corrInfo();
  await load();
  const timer = setInterval(load, 30000);
  return () => clearInterval(timer);
}

// „Wszedłem” tylko przy alercie faktycznie wysłanym na Telegram (ostatnie 12 h)
function enterLink(t) {
  const a = t.alert;
  if (!a) return '';
  if (a.taken) return `<a class="btn sm" data-enter href="#/positions" style="margin-top:14px;width:100%">Pozycja zapisana ✓</a>`;
  return `<a class="btn sm primary" data-enter href="#/positions?signal=${a.signal_id}" style="margin-top:14px;width:100%">Wszedłem — alert ${esc(a.direction)} ${fmtTime(a.ts, false)}</a>`;
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


// ------------------------------------------------------------ korekta cen do XTB
async function openCorrection(root, onSaved) {
  const host = root.querySelector('#corr-modal');
  let data;
  try { data = await api('/prices/offsets'); } catch (e) { return; }
  const age = (ts) => (ts ? `${Math.max(0, Math.round((Date.now() / 1000 - ts) / 86400))} d temu` : '—');
  host.innerHTML = `<div class="modal-bg"><div class="panel modal fade-in" style="max-width:760px">
    <div class="section-title" style="margin-top:0">Korekta cen do XTB</div>
    <div class="muted" style="font-size:12.5px;line-height:1.6;margin-bottom:12px">
      Wpisz <b>aktualną cenę z xStation</b> (z wykresu albo środek BID/ASK) przy instrumentach, które chcesz skorygować.
      Aplikacja przesunie wszystkie ceny o różnicę. Najlepiej robić to przy spokojnym rynku, bo dane źródła są opóźnione o kilka minut.
      Odśwież korektę raz w tygodniu.</div>
    <div class="table-wrap"><table class="t cards"><thead><tr><th>Instrument</th><th class="r">Cena w aplikacji</th><th class="r">Korekta</th><th class="r">Cena w XTB teraz</th><th></th></tr></thead><tbody>
    ${data.items.map((i) => `<tr>
      <td data-l="Instrument"><b>${esc(i.symbol)}</b>${i.cash_index ? '<div class="dim" style="font-size:11px">tylko w godz. sesji</div>' : ''}</td>
      <td data-l="W aplikacji" class="r mono">${fmt(i.src_price, i.digits)}<div class="dim" style="font-size:10.5px">${i.src_time ? fmtTime(i.src_time) : ''}</div></td>
      <td data-l="Korekta" class="r mono">${i.offset === null ? '<span class="dim">brak</span>' : `${i.offset > 0 ? '+' : ''}${fmt(i.offset, i.digits)}<div class="dim" style="font-size:10.5px">${age(i.calibrated_at)}</div>`}</td>
      <td data-l="Cena w XTB" class="r"><input type="number" step="any" data-sym="${esc(i.symbol)}" data-src="${i.src_price ?? ''}" data-d="${i.digits}" style="max-width:150px;text-align:right" placeholder="np. ${fmt(i.adjusted ?? i.src_price, i.digits).replace(/\s/g, '')}">
        <div class="muted" data-diff="${esc(i.symbol)}" style="font-size:11px;min-height:14px"></div></td>
      <td data-l="" class="r">${i.offset !== null ? `<button class="btn sm" data-rm="${esc(i.symbol)}" title="Usuń korektę">✕</button>` : ''}</td>
    </tr>`).join('')}</tbody></table></div>
    <div class="row" style="justify-content:flex-end;gap:8px;margin-top:14px"><button class="btn" id="corr-cancel">Anuluj</button><button class="btn primary" id="corr-save">Zapisz korektę</button></div>
  </div></div>`;
  const bg = host.querySelector('.modal-bg');
  const close = () => { host.innerHTML = ''; };
  bg.onclick = (e) => { if (e.target === bg) close(); };
  host.querySelector('#corr-cancel').onclick = close;
  host.querySelectorAll('input[data-sym]').forEach((inp) => inp.addEventListener('input', () => {
    const src = +inp.dataset.src, v = +inp.value, d = +inp.dataset.d;
    host.querySelector(`[data-diff="${inp.dataset.sym}"]`).textContent = inp.value && src ? `korekta ${v - src > 0 ? '+' : ''}${fmt(v - src, d)}` : '';
  }));
  const save = async (body) => {
    try {
      const r = await api('/prices/offsets', { method: 'PUT', json: body });
      const n = Object.keys(r.changed || {}).length;
      toast(n ? `Korekta zapisana (${n} instr.).` : 'Bez zmian.');
      close(); onSaved && onSaved();
    } catch (e) { toast(e.message, 'error'); }
  };
  host.querySelectorAll('[data-rm]').forEach((b) => { b.onclick = () => save({ [b.dataset.rm]: null }); });
  host.querySelector('#corr-save').onclick = () => {
    const body = {};
    host.querySelectorAll('input[data-sym]').forEach((inp) => { if (inp.value !== '') body[inp.dataset.sym] = +inp.value; });
    if (!Object.keys(body).length) { toast('Wpisz cenę z XTB przy co najmniej jednym instrumencie.', 'error'); return; }
    save(body);
  };
}
