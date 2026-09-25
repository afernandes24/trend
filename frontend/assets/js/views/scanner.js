// MARKET SCANNER — wszystkie instrumenty posortowane malejąco wg score.
import { api, bandClass, esc, fmt, fmtPct, mtfDots, scoreBar, sigBadge, signCls, ago } from '../lib.js';

export async function render(root, ctx) {
  root.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Market Scanner · setup 15M · sortowanie: Score ↓</h3>
        <div class="row"><span class="muted" id="scan-time" style="font-size:12px"></span>
          <button class="btn sm primary" id="btn-scan">Skanuj teraz</button></div>
      </div>
      <div class="table-wrap" id="scan-body"><div class="skeleton" style="height:420px;margin:16px"></div></div>
    </div>
    <p class="disclaimer">Lista jest sortowana wyłącznie według bieżącego score — kolejność nie jest rekomendacją.
      Kolumna „MTF" pokazuje trend na 4H / 1H / 15M / 5M (zielony = wzrost, czerwony = spadek).</p>`;

  async function load(refresh = false) {
    let data;
    try {
      data = await api(`/scanner${refresh ? '?refresh=true' : ''}`);
    } catch (e) {
      root.querySelector('#scan-body').innerHTML = `<div class="errbox" style="margin:16px">${esc(e.message)}</div>`;
      return;
    }
    if (!ctx.isCurrent()) return;
    root.querySelector('#scan-time').textContent = `Ostatni skan: ${ago(data.last_scan)}`;
    const rows = data.items.map((t, i) => `
      <tr class="click" data-sym="${esc(t.symbol)}">
        <td data-l="#" class="mono muted">${i + 1}.</td>
        <td data-l="Instrument"><b>${esc(t.symbol)}</b> <span class="muted" style="font-size:11.5px">${esc(t.name || '')}</span></td>
        <td data-l="Score" style="min-width:130px"><span class="mono" style="font-weight:700">${t.score ?? '—'}</span>${scoreBar(t.score)}</td>
        <td data-l="Ocena"><span class="band ${bandClass(t.band)}">${esc(t.band || '')}</span></td>
        <td data-l="Sygnał">${sigBadge(t.signal)}</td>
        <td data-l="Kandydat" class="${t.direction === 'LONG' ? 'long' : t.direction === 'SHORT' ? 'short' : 'muted'}">${esc(t.direction || '—')}</td>
        <td data-l="MTF">${mtfDots(t.mtf)}</td>
        <td data-l="RSI" class="r mono">${fmt(t.rsi, 1)}</td>
        <td data-l="ADX" class="r mono">${fmt(t.adx, 1)}</td>
        <td data-l="Zmiana" class="r mono ${signCls(t.change_pct)}">${fmtPct(t.change_pct)}</td>
        <td data-l="Cena" class="r mono">${fmt(t.price, t.digits)}</td>
      </tr>`).join('');
    root.querySelector('#scan-body').innerHTML = `
      <table class="t cards"><thead><tr>
        <th>#</th><th>Instrument</th><th>Score</th><th>Ocena</th><th>Sygnał</th><th>Kandydat</th><th>MTF</th>
        <th class="r">RSI</th><th class="r">ADX</th><th class="r">Zmiana</th><th class="r">Cena</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
    root.querySelectorAll('tr.click').forEach((tr) => {
      tr.onclick = () => { location.hash = `#/analysis/${encodeURIComponent(tr.dataset.sym)}`; };
    });
  }

  root.querySelector('#btn-scan').onclick = async (e) => {
    e.target.disabled = true;
    e.target.innerHTML = '<span class="spinner"></span> Skanowanie';
    await load(true);
    e.target.disabled = false;
    e.target.textContent = 'Skanuj teraz';
  };
  await load();
  const timer = setInterval(() => load(false), 30000);
  return () => clearInterval(timer);
}
