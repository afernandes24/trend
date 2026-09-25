// USTAWIENIA — parametry strategii skanera, status danych, import CSV.
import { api, esc, fmtTime, toast } from '../lib.js';

const FIELDS = [
  ['sma_fast', 'SMA szybka', 1], ['sma_mid', 'SMA średnia', 1], ['sma_slow', 'SMA wolna', 1],
  ['rsi_period', 'RSI okres', 1], ['rsi_long_min', 'RSI LONG od', 1], ['rsi_long_max', 'RSI LONG do', 1],
  ['rsi_short_min', 'RSI SHORT od', 1], ['rsi_short_max', 'RSI SHORT do', 1],
  ['atr_period', 'ATR okres', 1], ['adx_period', 'ADX okres', 1], ['adx_min', 'ADX minimum', 1], ['adx_strong', 'ADX silny trend', 1],
  ['macd_fast', 'MACD szybka', 1], ['macd_slow', 'MACD wolna', 1], ['macd_signal', 'MACD sygnał', 1],
  ['momentum_period', 'Momentum (ROC) okres', 1], ['swing_bars', 'Swing: świece z boku', 1],
  ['min_score', 'Minimalny score sygnału', 1],
  ['sl_atr_buffer', 'Bufor SL (×ATR)', 0.05], ['sl_min_atr', 'SL min (×ATR)', 0.1], ['sl_max_atr', 'SL max (×ATR)', 0.1],
  ['sl_swing_lookback', 'Swing dla SL: maks. świec wstecz', 1],
  ['rr_target', 'RR wyjścia (backtest / statystyki)', 0.1],
];

export async function render(root, ctx) {
  const [st, ds, insts] = await Promise.all([api('/settings/strategy'), api('/data/status'), api('/instruments')]);
  const p = st.params;
  root.innerHTML = `
  <div class="panel">
    <div class="panel-head"><h3>Parametry strategii (skaner, sygnały, alerty)</h3>
      <div class="row"><button class="btn sm" id="s-reset">Przywróć domyślne</button><button class="btn sm primary" id="s-save">Zapisz</button></div></div>
    <div class="panel-pad">
      <div class="form-grid">
        ${FIELDS.map(([k, l, step]) => `<div class="field"><label>${l}</label><input type="number" step="${step}" data-k="${k}" value="${p[k]}"></div>`).join('')}
        <div class="field"><label>TP1 / TP2 / TP3 (R)</label><input type="text" id="s-tpr" value="${p.tp_r.join(' / ')}"></div>
        <div class="field"><label>Tryb wyjścia</label><select id="s-exit"><option value="single" ${p.exit_mode === 'single' ? 'selected' : ''}>Całość na RR</option><option value="scaled" ${p.exit_mode === 'scaled' ? 'selected' : ''}>1/3 na TP1/TP2/TP3</option></select></div>
      </div>
      <div class="row" style="margin-top:14px;gap:22px">
        <label class="check"><input type="checkbox" id="s-mtf" ${p.require_mtf ? 'checked' : ''}> Sygnał wymaga zgodności 4H + 1H</label>
        <label class="check"><input type="checkbox" id="s-spread" ${p.use_spread ? 'checked' : ''}> Spread w statystykach historycznych</label>
      </div>
    </div>
  </div>

  <div class="panel" style="margin-top:16px">
    <div class="panel-head"><h3>Dane rynkowe</h3><span class="chip">${esc(ds.provider_description)}</span></div>
    <div class="table-wrap"><table class="t cards"><thead><tr><th>Instrument</th><th>Symbol źródła</th><th>5M: świece · zakres</th><th>1H: świece · zakres</th><th>Ostatnie pobranie</th></tr></thead><tbody>
      ${ds.instruments.map((r) => {
        const f = r.fetch || {};
        const err = [f['5M']?.error, f['1H']?.error].filter(Boolean)[0];
        const rng = (x) => (x ? `${x.bars} · ${fmtTime(x.from)} → ${fmtTime(x.to)}` : '<span class="dim">brak</span>');
        return `<tr><td data-l="Instrument"><b>${esc(r.symbol)}</b><div class="dim" style="font-size:11px;white-space:normal;max-width:280px">${esc(r.proxy_note)}</div></td>
          <td data-l="Źródło" class="mono">${esc(r.source_symbol || '—')}</td>
          <td data-l="5M" class="mono" style="font-size:12px">${rng(r['5M'])}</td><td data-l="1H" class="mono" style="font-size:12px">${rng(r['1H'])}</td>
          <td data-l="Pobranie" style="white-space:normal;max-width:300px">${err ? `<span class="short" style="font-size:12px">${esc(err)}</span>` : f['5M'] ? `<span class="long">OK</span> <span class="dim">${fmtTime(f['5M'].updated)}</span>` : '<span class="dim">—</span>'}</td></tr>`;
      }).join('')}</tbody></table></div>
    <div class="panel-pad"><div class="notebox">Aplikacja pobiera świece 5M i 1H, a 15M / 4H buduje z nich lokalnie. Źródło ustawiasz w <span class="mono">.env</span> (<span class="mono">MARKET_DATA_PROVIDER</span>: yahoo / csv / xtb).
      Dane Yahoo to kontrakty futures lub indeksy kasowe — poziomy cen mogą różnić się od CFD w XTB. Dłuższą historię (np. do backtestu) dodasz importem CSV.</div></div>
  </div>

  <div class="panel" style="margin-top:16px">
    <div class="panel-head"><h3>Import CSV</h3></div>
    <div class="panel-pad">
      <div class="form-grid">
        <div class="field"><label>Instrument</label><select id="i-sym">${insts.map((i) => `<option>${esc(i.symbol)}</option>`).join('')}</select></div>
        <div class="field"><label>Interwał pliku</label><select id="i-tf"><option>5M</option><option>15M</option><option selected>1H</option><option>4H</option><option>1D</option></select></div>
        <div class="field"><label>Strefa czasu w pliku</label><select id="i-tz"><option>UTC</option><option>Europe/Warsaw</option><option>America/New_York</option></select></div>
        <div class="field" style="grid-column: span 2"><label>Plik CSV</label><input type="file" id="i-file" accept=".csv,text/csv"></div>
      </div>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="i-go">Importuj</button>
        <span class="muted" style="font-size:12px">Kolumny: time/date, open, high, low, close, [volume]. Separator , lub ;</span></div>
    </div>
  </div>`;

  const $ = (id) => root.querySelector(id);
  $('#s-save').onclick = async () => {
    const body = {};
    root.querySelectorAll('[data-k]').forEach((el) => { body[el.dataset.k] = +el.value; });
    body.tp_r = $('#s-tpr').value.split(/[\/;, ]+/).map(Number).filter((x) => x > 0);
    body.exit_mode = $('#s-exit').value;
    body.require_mtf = $('#s-mtf').checked;
    body.use_spread = $('#s-spread').checked;
    try { await api('/settings/strategy', { method: 'PUT', json: body }); toast('Parametry zapisane. Skaner użyje ich przy następnym przeliczeniu.'); } catch (e) { toast(e.message, 'error'); }
  };
  $('#s-reset').onclick = async () => {
    try { await api('/settings/strategy', { method: 'PUT', json: st.defaults }); toast('Przywrócono domyślne.'); location.reload(); } catch (e) { toast(e.message, 'error'); }
  };
  $('#i-go').onclick = async () => {
    const file = $('#i-file').files[0];
    if (!file) { toast('Wybierz plik CSV.', 'error'); return; }
    const fd = new FormData();
    fd.append('symbol', $('#i-sym').value);
    fd.append('tf', $('#i-tf').value);
    fd.append('tz', $('#i-tz').value);
    fd.append('file', file);
    try {
      const res = await fetch('/api/data/import', { method: 'POST', body: fd });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      toast(`Zaimportowano ${j.imported} świec (${j.symbol} ${j.tf}).`);
    } catch (e) { toast(e.message, 'error'); }
  };
}
