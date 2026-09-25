// BACKTESTING — parametry, metryki, TRAINING/TESTING, walk-forward, equity curve.
import { api, esc, fmt, fmtDate, fmtR, fmtTime, qs, signCls, store } from '../lib.js';
import { createEquityChart } from '../chart.js';

const TFS = ['5M', '15M', '1H', '4H'];

export async function render(root, ctx) {
  const [insts, st] = await Promise.all([api('/instruments'), api('/settings/strategy')]);
  const p = st.params;
  const q = qs();
  const last = store.get('bt', {});
  const sym = q.symbol || last.symbol || 'US100';
  const tf = q.tf || last.tf || '1H';
  const num = (id, label, val, step = 1, extra = '') => `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="number" step="${step}" value="${val}" ${extra}></div>`;

  root.innerHTML = `
  <div class="panel">
    <div class="panel-head"><h3>Konfiguracja backtestu</h3><span class="muted" style="font-size:12px" id="bt-range"></span></div>
    <div class="panel-pad">
      <div class="form-grid">
        <div class="field"><label>Instrument</label><select id="b-sym">${insts.map((i) => `<option ${i.symbol === sym ? 'selected' : ''}>${esc(i.symbol)}</option>`).join('')}</select></div>
        <div class="field"><label>Interwał</label><select id="b-tf">${TFS.map((t) => `<option ${t === tf ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
        <div class="field"><label>Od (opcjonalnie)</label><input id="b-from" type="date" value="${last.from || ''}"></div>
        <div class="field"><label>Do (opcjonalnie)</label><input id="b-to" type="date" value="${last.to || ''}"></div>
        ${num('b-sma1', 'SMA szybka', p.sma_fast)}
        ${num('b-sma2', 'SMA średnia', p.sma_mid)}
        ${num('b-sma3', 'SMA wolna', p.sma_slow)}
        ${num('b-rsi', 'RSI okres', p.rsi_period)}
        ${num('b-rsilo', 'RSI LONG od', p.rsi_long_min)}
        ${num('b-rsihi', 'RSI LONG do', p.rsi_long_max)}
        ${num('b-atr', 'ATR okres', p.atr_period)}
        ${num('b-adx', 'ADX okres', p.adx_period)}
        ${num('b-adxmin', 'ADX minimum', p.adx_min)}
        ${num('b-score', 'Minimalny score', p.min_score, 1, 'min="0" max="100"')}
        ${num('b-rr', 'Risk/Reward (wyjście)', p.rr_target, 0.1)}
        <div class="field"><label>Tryb wyjścia</label><select id="b-exit"><option value="single" ${p.exit_mode === 'single' ? 'selected' : ''}>Całość na RR</option><option value="scaled" ${p.exit_mode === 'scaled' ? 'selected' : ''}>1/3 na TP1/TP2/TP3</option></select></div>
        <div class="field"><label>TP1 / TP2 / TP3 (R)</label><input id="b-tpr" type="text" value="${p.tp_r.join(' / ')}"></div>
        ${num('b-slbuf', 'Bufor SL (×ATR)', p.sl_atr_buffer, 0.05)}
        ${num('b-slmin', 'SL min (×ATR)', p.sl_min_atr, 0.1)}
        ${num('b-slmax', 'SL max (×ATR)', p.sl_max_atr, 0.1)}
        ${num('b-maxbars', 'Maks. świec w pozycji (0 = ∞)', p.max_bars_in_trade)}
        ${num('b-cap', 'Kapitał (do krzywej)', last.capital || 10000, 100)}
        ${num('b-riskpct', 'Ryzyko / transakcja %', last.risk_pct || 1, 0.1)}
        ${num('b-split', 'TRAINING % (reszta = TESTING)', last.split ?? 70, 5, 'min="10" max="90"')}
      </div>
      <div class="row" style="margin-top:16px;gap:22px">
        <label class="check"><input type="checkbox" id="b-spread" ${p.use_spread ? 'checked' : ''}> Uwzględnij spread</label>
        <label class="check"><input type="checkbox" id="b-wf" ${last.wf ? 'checked' : ''}> Walk-forward</label>
        <div class="field" style="flex-direction:row;align-items:center;gap:8px"><label>Kroki</label><input id="b-folds" type="number" min="2" max="12" value="${last.folds || 4}" style="width:70px"></div>
        <div class="field" style="flex-direction:row;align-items:center;gap:8px"><label>Okno</label><select id="b-wfmode" style="width:auto"><option value="rolling">rolling</option><option value="anchored" ${last.wfmode === 'anchored' ? 'selected' : ''}>anchored</option></select></div>
        <button class="btn primary" id="b-run" style="margin-left:auto">Uruchom backtest</button>
      </div>
    </div>
  </div>
  <div id="bt-out" style="margin-top:16px"></div>
  <p class="disclaimer">Symulacja: sygnał na zamknięciu świecy, wejście po otwarciu kolejnej, SL przed TP gdy oba w jednej świecy,
    spread z rejestru instrumentów, jedna pozycja naraz. Wyniki historyczne nie gwarantują przyszłych wyników.
    Walk-forward dobiera tylko minimalny score i RR na oknie treningowym i ocenia je na kolejnym, niewidzianym oknie.</p>`;

  const $ = (id) => root.querySelector(id);
  let charts = [];

  async function run() {
    const tpr = $('#b-tpr').value.split(/[\/;, ]+/).map(Number).filter((x) => x > 0);
    const body = {
      symbol: $('#b-sym').value, tf: $('#b-tf').value,
      date_from: $('#b-from').value || null, date_to: $('#b-to').value || null,
      split: +$('#b-split').value / 100, capital: +$('#b-cap').value, risk_pct: +$('#b-riskpct').value,
      walkforward: $('#b-wf').checked, wf_folds: +$('#b-folds').value, wf_mode: $('#b-wfmode').value,
      params: {
        sma_fast: +$('#b-sma1').value, sma_mid: +$('#b-sma2').value, sma_slow: +$('#b-sma3').value,
        rsi_period: +$('#b-rsi').value, rsi_long_min: +$('#b-rsilo').value, rsi_long_max: +$('#b-rsihi').value,
        rsi_short_min: 100 - +$('#b-rsihi').value, rsi_short_max: 100 - +$('#b-rsilo').value,
        atr_period: +$('#b-atr').value, adx_period: +$('#b-adx').value, adx_min: +$('#b-adxmin').value,
        min_score: +$('#b-score').value, rr_target: +$('#b-rr').value, exit_mode: $('#b-exit').value, tp_r: tpr,
        sl_atr_buffer: +$('#b-slbuf').value, sl_min_atr: +$('#b-slmin').value, sl_max_atr: +$('#b-slmax').value,
        max_bars_in_trade: +$('#b-maxbars').value, use_spread: $('#b-spread').checked,
      },
    };
    store.set('bt', { symbol: body.symbol, tf: body.tf, from: body.date_from, to: body.date_to, capital: body.capital,
      risk_pct: body.risk_pct, split: +$('#b-split').value, wf: body.walkforward, folds: body.wf_folds, wfmode: body.wf_mode });
    const btn = $('#b-run');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Liczenie…';
    charts.forEach((c) => c.destroy());
    charts = [];
    $('#bt-out').innerHTML = '<div class="panel skeleton" style="height:300px"></div>';
    try {
      const res = await api('/backtest', { json: body });
      if (!ctx.isCurrent()) return;
      $('#bt-out').innerHTML = results(res);
      const eqEl = $('#eq');
      if (eqEl && res.equity.length) {
        const series = [{ data: res.equity, color: '#f4f4f5', fill: 'rgba(244,244,245,.08)' }];
        const markers = res.split ? [{ time: Math.floor(new Date(res.split.cut_time).getTime() / 1000), position: 'aboveBar', color: '#a5a1ff', shape: 'arrowDown', text: 'TESTING →' }] : [];
        // znacznik musi wskazywać istniejący punkt — wybieramy pierwszy punkt po podziale
        const cut = markers[0]?.time;
        const mt = cut ? res.equity.find((e) => e.time >= cut) : null;
        charts.push(createEquityChart(eqEl, series.map((s) => ({ ...s, data: s.data.map((e) => ({ time: e.time, value: e.r })) })), mt ? [{ ...markers[0], time: mt.time }] : []));
      }
      const wfEl = $('#eq-wf');
      if (wfEl && res.walkforward?.equity.length) {
        charts.push(createEquityChart(wfEl, [{ data: res.walkforward.equity.map((e) => ({ time: e.time, value: e.r })), color: '#3ddc97', fill: 'rgba(61,220,151,.08)' }]));
      }
    } catch (e) {
      $('#bt-out').innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Uruchom backtest';
    }
  }
  $('#b-run').onclick = run;
  if (q.symbol) run();
  return () => charts.forEach((c) => c.destroy());
}

function metricGrid(m) {
  const cell = (k, v, cls = '') => `<div class="metric"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
  return `<div class="metric-grid">
    ${cell('Liczba transakcji', m.trades)}
    ${cell('Win rate', m.win_rate === null ? '—' : fmt(m.win_rate, 1) + '%')}
    ${cell('Średni zysk', fmtR(m.avg_win_r), 'long')}
    ${cell('Średnia strata', fmtR(m.avg_loss_r), 'short')}
    ${cell('Profit factor', m.profit_factor ?? '—')}
    ${cell('Expectancy', fmtR(m.expectancy_r, 3), signCls(m.expectancy_r))}
    ${cell('Maks. drawdown', `${fmt(m.max_drawdown_r, 2)}R <span class="dim" style="font-size:11px">${fmt(m.max_drawdown_pct, 1)}%</span>`, 'short')}
    ${cell('Najdł. seria strat', m.max_loss_streak)}
    ${cell('Najdł. seria zysków', m.max_win_streak)}
    ${cell('Wynik całkowity', fmtR(m.total_r), signCls(m.total_r))}
    ${cell('Wynik LONG', `${fmtR(m.long.total_r)} <span class="dim" style="font-size:11px">(${m.long.trades})</span>`, signCls(m.long.total_r))}
    ${cell('Wynik SHORT', `${fmtR(m.short.total_r)} <span class="dim" style="font-size:11px">(${m.short.trades})</span>`, signCls(m.short.total_r))}
  </div>`;
}

function compareRow(label, a, b, f) {
  return `<tr><td>${label}</td><td class="r mono">${f(a)}</td><td class="r mono">${f(b)}</td></tr>`;
}

function results(r) {
  const m = r.all;
  const pct = (v) => (v === null || v === undefined ? '—' : fmt(v, 1) + '%');
  let html = `
  <div class="panel">
    <div class="panel-head"><h3>Wyniki · ${esc(r.symbol)} ${esc(r.tf)}</h3>
      <span class="muted" style="font-size:12px">${fmtDate(r.from)} – ${fmtDate(r.to)} · ${r.bars} świec · spread ${r.spread}</span></div>
    <div class="panel-pad">
      ${m.trades === 0 ? '<div class="notebox" style="margin-bottom:12px">Brak transakcji dla tej konfiguracji. Obniż minimalny score lub wydłuż okres.</div>' : ''}
      ${m.trades > 0 && m.trades < 30 ? '<div class="warnbox" style="margin:0 0 12px">Mała próba (&lt; 30 transakcji) — statystyki mają dużą niepewność.</div>' : ''}
      ${metricGrid(m)}
      <div class="muted" style="font-size:12px;margin-top:10px">Kapitał ${fmt(r.capital, 0)} przy ryzyku ${fmt(r.risk_pct, 1)}%/transakcję → ${fmt(m.final_equity, 0)} (${m.return_pct > 0 ? '+' : ''}${fmt(m.return_pct, 1)}%). Śr. czas w pozycji: ${fmt(m.avg_bars_held, 1)} świec.</div>
      <div class="section-title">Equity curve (R)</div>
      <div class="equity" id="eq"></div>
    </div>
  </div>`;
  if (r.split) {
    const a = r.split.train, b = r.split.test;
    html += `
    <div class="panel" style="margin-top:16px">
      <div class="panel-head"><h3>TRAINING / TESTING (${Math.round(r.split.ratio * 100)}% / ${Math.round(100 - r.split.ratio * 100)}%)</h3>
        <span class="muted" style="font-size:12px">podział: ${fmtTime(r.split.cut_time)}</span></div>
      <div class="table-wrap"><table class="t"><thead><tr><th>Metryka</th><th class="r">TRAINING<br><span class="dim">${fmtDate(a.from)}–${fmtDate(a.to)}</span></th><th class="r">TESTING<br><span class="dim">${fmtDate(b.from)}–${fmtDate(b.to)}</span></th></tr></thead><tbody>
        ${compareRow('Transakcje', a.trades, b.trades, (v) => v)}
        ${compareRow('Win rate', a.win_rate, b.win_rate, pct)}
        ${compareRow('Expectancy', a.expectancy_r, b.expectancy_r, (v) => fmtR(v, 3))}
        ${compareRow('Profit factor', a.profit_factor, b.profit_factor, (v) => v ?? '—')}
        ${compareRow('Wynik', a.total_r, b.total_r, (v) => fmtR(v))}
        ${compareRow('Maks. DD', a.max_drawdown_r, b.max_drawdown_r, (v) => fmt(v, 2) + 'R')}
      </tbody></table></div>
      <div class="muted" style="font-size:12px;padding:10px 18px 14px">Jeśli wynik TESTING jest wyraźnie gorszy od TRAINING, konfiguracja jest prawdopodobnie dopasowana do historii (overfitting).</div>
    </div>`;
  }
  if (r.walkforward) {
    const w = r.walkforward;
    const rows = w.folds.map((f) => `<tr>
      <td data-l="Krok">${f.fold}</td>
      <td data-l="Trening" class="mono">${fmtDate(f.train_from)} – ${fmtDate(f.train_to)}</td>
      <td data-l="Test" class="mono">${fmtDate(f.test_from)} – ${fmtDate(f.test_to)}</td>
      <td data-l="Wybrane">${f.chosen ? `score ≥ ${f.chosen.min_score}, RR ${f.chosen.rr_target}` : `<span class="warn">${esc(f.note)}</span>`}</td>
      <td data-l="Trening exp." class="r mono">${f.train ? fmtR(f.train.expectancy_r, 3) : '—'}</td>
      <td data-l="Test trans." class="r mono">${f.test ? f.test.trades : '—'}</td>
      <td data-l="Test exp." class="r mono ${signCls(f.test?.expectancy_r)}">${f.test ? fmtR(f.test.expectancy_r, 3) : '—'}</td>
      <td data-l="Test wynik" class="r mono ${signCls(f.test?.total_r)}">${f.test ? fmtR(f.test.total_r) : '—'}</td></tr>`).join('');
    html += `
    <div class="panel" style="margin-top:16px">
      <div class="panel-head"><h3>Walk-forward (${esc(w.mode)}, ${w.folds.length} kroków) — wynik OUT-OF-SAMPLE</h3>
        <span class="muted" style="font-size:12px">siatka: score ${w.score_grid.join('/')} × RR ${w.rr_grid.join('/')}</span></div>
      <div class="table-wrap"><table class="t cards"><thead><tr><th>Krok</th><th>Trening</th><th>Test</th><th>Wybrane na treningu</th><th class="r">Exp. trening</th><th class="r">Trans. test</th><th class="r">Exp. test</th><th class="r">Wynik test</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="panel-pad">${metricGrid(w.oos)}<div class="section-title">Equity OOS (R)</div><div class="equity" id="eq-wf"></div></div>
    </div>`;
  }
  if (r.trades && r.trades.length) {
    const rows = r.trades.slice(-300).reverse().map((t) => `<tr>
      <td data-l="Wejście" class="mono">${fmtTime(t.entry_time)}</td>
      <td data-l="Kierunek" class="${t.direction === 'LONG' ? 'long' : 'short'}">${t.direction}</td>
      <td data-l="Score" class="mono">${t.score}</td>
      <td data-l="Entry" class="r mono">${fmt(t.entry, r.digits)}</td>
      <td data-l="SL" class="r mono">${fmt(t.sl, r.digits)}</td>
      <td data-l="Wyjście" class="r mono">${fmt(t.exit, r.digits)}</td>
      <td data-l="Powód">${esc(t.exit_reason)}</td>
      <td data-l="Świece" class="r mono">${t.bars_held}</td>
      <td data-l="Wynik" class="r mono ${signCls(t.r)}">${fmtR(t.r)}</td></tr>`).join('');
    html += `<div class="panel" style="margin-top:16px"><div class="panel-head"><h3>Transakcje (${r.trades.length}${r.trades.length > 300 ? ', pokazano 300 ostatnich' : ''})</h3></div>
      <div class="table-wrap" style="max-height:520px"><table class="t cards"><thead><tr><th>Wejście</th><th>Kierunek</th><th>Score</th><th class="r">Entry</th><th class="r">SL</th><th class="r">Wyjście</th><th>Powód</th><th class="r">Świece</th><th class="r">Wynik</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }
  return html;
}
