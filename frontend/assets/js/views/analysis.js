// WYKRES + ANALIZA: świece, SMA, poziomy, MTF, karta sygnału, dane historyczne, kalkulator.
import { api, bandClass, esc, fmt, fmtDate, fmtTime, qs, sigBadge, store } from '../lib.js';
import { createTradingChart } from '../chart.js';
import { renderCalculator } from './risk.js';

const TFS = ['5M', '15M', '1H', '4H'];

export async function render(root, ctx) {
  const insts = await api('/instruments');
  const symbol = decodeURIComponent(ctx.args[0] || store.get('lastSymbol', 'US100'));
  const tf = (qs().tf || store.get('lastTf', '15M')).toUpperCase();
  store.set('lastSymbol', symbol);
  store.set('lastTf', tf);
  ctx.setTitle(`${symbol} · ${tf}`);

  root.innerHTML = `
    <div class="row between" style="margin-bottom:16px">
      <div class="row">
        <select id="a-sym" style="width:auto;min-width:130px">${insts.map((i) => `<option ${i.symbol === symbol ? 'selected' : ''}>${esc(i.symbol)}</option>`).join('')}</select>
        <div class="seg" id="a-tf">${TFS.map((t) => `<button class="${t === tf ? 'on' : ''}" data-tf="${t}">${t}</button>`).join('')}</div>
      </div>
      <div class="row" id="a-badges"></div>
    </div>
    <div class="analysis-grid">
      <div class="stack">
        <div class="panel chart-box">
          <div class="chart-legend" id="legend">
            <span data-k="sma_fast"><i style="background:rgba(244,244,245,.75)"></i>SMA 20</span>
            <span data-k="sma_mid"><i style="background:#a5a1ff"></i>SMA 50</span>
            <span data-k="sma_slow"><i style="background:#7cc4ff"></i>SMA 200</span>
            <span data-k="levels"><i style="background:#f4f4f5"></i>Entry <i style="background:#ff5a6e"></i>SL <i style="background:#3ddc97"></i>TP</span>
            <span data-k="markers">▲▼ Sygnały BUY/SELL</span>
            <span data-k="sr"><i style="background:rgba(160,170,195,.6)"></i>Wsparcie / opór</span>
          </div>
          <div class="chart-main" id="c-main"><div class="skeleton" style="height:100%;border-radius:0"></div></div>
          <div class="sub-label">RSI</div><div class="chart-sub" id="c-rsi"></div>
          <div class="sub-label">MACD</div><div class="chart-sub" id="c-macd"></div>
        </div>
        <div class="panel" id="mtf-panel"></div>
      </div>
      <div class="stack" id="side"><div class="panel skeleton" style="height:520px"></div></div>
    </div>
    <div id="calc" style="margin-top:16px"></div>`;

  root.querySelector('#a-sym').onchange = (e) => { location.hash = `#/analysis/${encodeURIComponent(e.target.value)}?tf=${tf}`; };
  root.querySelectorAll('#a-tf button').forEach((b) => { b.onclick = () => { location.hash = `#/analysis/${encodeURIComponent(symbol)}?tf=${b.dataset.tf}`; }; });

  let chart = null;
  let a, c;
  try {
    [a, c] = await Promise.all([api(`/analysis/${encodeURIComponent(symbol)}`), api(`/chart/${encodeURIComponent(symbol)}?tf=${tf}&bars=500`)]);
  } catch (e) {
    root.querySelector('#side').innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
    return;
  }
  if (!ctx.isCurrent()) return;

  const tfA = a.timeframes[tf] || {};
  root.querySelector('#a-badges').innerHTML = `
    <span class="chip">Sygnał (setup ${a.setup_tf}, MTF): ${sigBadge(a.signal)}</span>
    <span class="chip">${a.mtf?.all_aligned ? '<span class="dot ok"></span>Wszystkie interwały zgodne' : a.mtf?.higher_tf_aligned ? '<span class="dot ok"></span>4H + 1H zgodne' : '<span class="dot"></span>Interwały niezgodne'}</span>`;

  if (!c.ok) {
    root.querySelector('#c-main').innerHTML = `<div class="empty">${esc(c.error || 'Brak danych')}. <a href="#/settings">Sprawdź źródło danych</a>.</div>`;
  } else {
    const lv = tfA.ok ? tfA.levels : null;
    root.querySelector('#c-main').innerHTML = '';
    chart = createTradingChart(
      { main: root.querySelector('#c-main'), rsi: root.querySelector('#c-rsi'), macd: root.querySelector('#c-macd') },
      c, lv, tfA.ok ? tfA.sr : null, a.digits,
    );
    const hidden = store.get('legendOff', []);
    root.querySelectorAll('#legend span').forEach((s) => {
      const k = s.dataset.k;
      if (hidden.includes(k)) { s.classList.add('off'); chart.toggle(k, false); }
      s.onclick = () => {
        const off = s.classList.toggle('off');
        chart.toggle(k, !off);
        const cur = new Set(store.get('legendOff', []));
        off ? cur.add(k) : cur.delete(k);
        store.set('legendOff', [...cur]);
      };
    });
  }

  root.querySelector('#mtf-panel').innerHTML = mtfPanel(a);
  root.querySelector('#side').innerHTML = signalCard(a, tf, tfA) + historyBox(a) + indicatorsBox(tfA, a.digits);

  const btn = root.querySelector('#btn-analiza');
  if (btn) {
    btn.onclick = async () => {
      const lv = tfA.levels || {};
      await renderCalculator(root.querySelector('#calc'), {
        symbol, entry: round(lv.entry, a.digits), sl: round(lv.sl, a.digits), direction: tfA.direction, tp_r: lv.tp_r,
      });
      root.querySelector('#calc').scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  }
  return () => chart && chart.destroy();
}

const round = (v, d) => (v === null || v === undefined ? '' : Number(v.toFixed(d)));

function signalCard(a, tf, t) {
  if (!t.ok) {
    return `<div class="panel signal-card"><div class="errbox">${esc(t.error || 'Brak danych dla interwału')}</div></div>`;
  }
  const d = a.digits;
  const isSetupTf = tf === a.setup_tf;
  const sig = isSetupTf ? a.signal : (t.active ? t.direction : 'WAIT');
  const lv = t.levels || {};
  const color = sig === 'LONG' ? 'var(--long)' : sig === 'SHORT' ? 'var(--short)' : 'var(--text-2)';
  const rr = (lv.tp_r || []).map((r) => `1:${r}`).join(' / ');
  const title = sig;
  const reasons = (t.components || []).map((r) => `
      <li class="${r.status}"><span class="ic">${r.status === 'ok' ? '✓' : r.status === 'partial' ? '~' : '✗'}</span>
      <span>${esc(r.label)}</span><span class="pts">${fmt(r.points, 0)}/${r.max}</span></li>`).join('');
  const warnings = [];
  if (sig === 'WAIT') {
    if (!t.gate) warnings.push(`Warunki trendu ${t.direction} na ${tf} nie są spełnione (cena/SMA200, SMA50/200, SMA20/50, ADX ≥ min, DI).`);
    else if (!t.active) warnings.push(`Score ${t.score} poniżej progu ${a.min_score}.`);
    if (isSetupTf && t.active && !a.mtf?.higher_tf_aligned) warnings.push('Setup na 15M jest, ale 4H/1H nie potwierdzają kierunku.');
  }
  (lv.warnings || []).forEach((w) => warnings.push(w));
  return `
  <div class="panel signal-card fade-in">
    <div class="sig-head">
      <div>
        <div class="muted" style="font-size:11px;letter-spacing:.12em;font-weight:700">${isSetupTf ? 'BIEŻĄCY SYGNAŁ' : `ANALIZA INTERWAŁU ${tf}`}</div>
        <div class="sig-title ${sig === 'LONG' ? 'long' : sig === 'SHORT' ? 'short' : 'muted'}">${title}</div>
        ${sig === 'WAIT' ? `<div class="muted" style="font-size:12px">Kandydat: <b class="${t.direction === 'LONG' ? 'long' : 'short'}">${t.direction}</b> — poziomy orientacyjne</div>` : ''}
      </div>
      <div class="ring" style="--v:${t.score};--c:${color}"><div><div><div class="rv">${t.score}</div><div class="rs">/100</div></div></div></div>
    </div>
    <div class="row between" style="margin-top:10px">
      <span class="muted">Instrument <b style="color:var(--text)">${esc(a.symbol)}</b> · Timeframe <b style="color:var(--text)">${tf}</b></span>
      <span class="band ${bandClass(t.band)}">${esc(t.band)}</span>
    </div>
    <div class="levels">
      <span class="lk"><i style="background:var(--text)"></i>Entry</span><span class="lv">${fmt(lv.entry, d)}</span>
      <span class="lk"><i style="background:var(--short)"></i>Stop Loss</span><span class="lv">${fmt(lv.sl, d)}</span>
      <span class="lk"><i style="background:var(--long)"></i>Take Profit 1 <span class="dim">(${lv.tp_r?.[0]}R)</span></span><span class="lv">${fmt(lv.tp1, d)}</span>
      <span class="lk"><i style="background:var(--long)"></i>Take Profit 2 <span class="dim">(${lv.tp_r?.[1]}R)</span></span><span class="lv">${fmt(lv.tp2, d)}</span>
      <span class="lk"><i style="background:var(--long)"></i>Take Profit 3 <span class="dim">(${lv.tp_r?.[2]}R)</span></span><span class="lv">${fmt(lv.tp3, d)}</span>
      <span class="lk">Risk/Reward</span><span class="lv">${rr}</span>
      <span class="lk">Ryzyko (odl. SL)</span><span class="lv">${fmt(lv.risk, d)} <span class="dim">· ${fmt(lv.risk_atr, 2)}×ATR</span></span>
    </div>
    <div class="dim" style="font-size:11.5px">SL: ${esc(lv.sl_method || '')}. Entry = zamknięcie świecy ${fmtTime(t.bar_time)}.</div>
    <div class="section-title" style="margin:16px 0 4px">Powody wejścia · score</div>
    <ul class="reasons">${reasons}</ul>
    ${warnings.map((w) => `<div class="warnbox">${esc(w)}</div>`).join('')}
    <button class="btn analysis" id="btn-analiza" style="width:100%;margin-top:16px">ANALIZA</button>
    <div class="dim" style="font-size:11px;margin-top:8px;text-align:center">Otwiera kalkulator pozycji. Aplikacja nie otwiera pozycji na rachunku.</div>
  </div>`;
}

function historyBox(a) {
  const h = a.historical;
  if (!h) return '';
  if (!h.trades) {
    return `<div class="panel panel-pad"><div class="hist-box" style="margin:0"><div class="ht">DANE HISTORYCZNE (BACKTEST)</div>
      Brak transakcji w backteście dla tej konfiguracji. ${esc(h.note || '')}</div></div>`;
  }
  return `<div class="panel panel-pad"><div class="hist-box" style="margin:0">
    <div class="ht">DANE HISTORYCZNE (BACKTEST) — NIE PROGNOZA</div>
    <div style="font-size:15px;font-weight:650">Historical win rate: <span class="mono">${fmt(h.win_rate, 1)}%</span></div>
    <div>Based on <b class="mono">${h.trades}</b> trades · ${esc(a.symbol)} ${esc(a.setup_tf)}</div>
    <div class="muted">Okres testu: ${fmtDate(h.from)} – ${fmtDate(h.to)}</div>
    <div class="muted">Expectancy ${h.expectancy_r === null ? '—' : fmt(h.expectancy_r, 2) + 'R'} · PF ${h.profit_factor ?? '—'} · min score ${h.min_score} · wyjście ${h.exit_mode === 'single' ? `RR ${h.rr_target}` : 'TP1/TP2/TP3'}</div>
    ${h.small_sample ? '<div class="warn" style="margin-top:6px">Mała próba (&lt; 20 transakcji) — wynik mało wiarygodny.</div>' : ''}
    <div style="margin-top:8px"><a href="#/backtest?symbol=${encodeURIComponent(a.symbol)}&tf=${a.setup_tf}">Pełny backtest i walk-forward →</a></div>
  </div></div>`;
}

function indicatorsBox(t, d) {
  if (!t.ok) return '';
  const i = t.indicators;
  const row = (k, v) => `<span class="lk">${k}</span><span class="lv">${v}</span>`;
  return `<div class="panel panel-pad"><div class="section-title" style="margin:0 0 6px">Wskaźniki (${t.tf})</div><div class="levels" style="margin:0">
    ${row('SMA 20 / 50', `${fmt(i.sma_fast, d)} / ${fmt(i.sma_mid, d)}`)}
    ${row('SMA 200', fmt(i.sma_slow, d))}
    ${row('RSI 14', fmt(i.rsi, 1))}
    ${row('ADX 14 (+DI / −DI)', `${fmt(i.adx, 1)} (${fmt(i.plus_di, 1)} / ${fmt(i.minus_di, 1)})`)}
    ${row('MACD / sygnał', `${fmt(i.macd, d)} / ${fmt(i.macd_signal, d)}`)}
    ${row('ATR 14', `${fmt(i.atr, d)} <span class="dim">(${fmt(i.atr_ratio, 2)}× mediany)</span>`)}
    ${row('Momentum (ROC)', `${fmt(i.roc, 2)}%`)}
    ${row('Wolumen', i.has_volume ? 'dostępny' : 'brak — użyto zakresu świec')}
  </div></div>`;
}

function mtfPanel(a) {
  const rows = (a.mtf?.rows || []).map((r) => `
    <tr><td data-l="TF">${r.tf}</td><td data-l="Rola">${esc(r.role)}</td>
      <td data-l="Trend">${r.trend === 1 ? '<span class="long">▲ wzrost</span>' : r.trend === -1 ? '<span class="short">▼ spadek</span>' : r.trend === 0 ? '<span class="muted">◆ brak</span>' : '<span class="dim">brak danych</span>'}</td>
      <td data-l="Score" class="mono">${r.score ?? '—'}</td>
      <td data-l="Kandydat" class="${r.direction === 'LONG' ? 'long' : r.direction === 'SHORT' ? 'short' : 'muted'}">${r.direction || '—'}</td>
      <td data-l="Zgodność">${r.agree === true ? '<span class="long">✓ zgodny</span>' : r.agree === false ? '<span class="short">✗ niezgodny</span>' : '<span class="dim">—</span>'}</td></tr>`).join('');
  return `<div class="panel-head"><h3>Multi-timeframe · kierunek ${a.mtf?.direction || '—'}</h3>
      <span class="chip">${a.mtf?.all_aligned ? 'Wszystkie zgodne' : a.mtf?.higher_tf_aligned ? '4H + 1H zgodne' : 'Brak pełnej zgodności'}</span></div>
    <div class="table-wrap"><table class="t cards mtf-table"><thead><tr><th>TF</th><th>Rola</th><th>Trend</th><th>Score</th><th>Kandydat</th><th>Zgodność z setupem</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}
