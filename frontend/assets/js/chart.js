// Wykres świecowy (TradingView Lightweight Charts) + panele RSI i MACD.
/* global LightweightCharts */

const C = {
  up: '#3ddc97', down: '#ff5a6e', text: '#71717a', grid: 'rgba(255,255,255,0.035)',
  sma1: 'rgba(244,244,245,0.75)', sma2: '#a5a1ff', sma3: '#7cc4ff', entry: '#f4f4f5', sl: '#ff5a6e', tp: '#3ddc97',
  sr: 'rgba(161,161,170,0.45)',
};
// Lightweight Charts rysuje w UTC — przesuwamy czas o strefę przeglądarki.
const TZ = -new Date().getTimezoneOffset() * 60;
const lt = (t) => t + TZ;

function baseOptions(height) {
  return {
    autoSize: true,
    height,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.07)' },
    timeScale: { borderColor: 'rgba(255,255,255,0.07)', timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: 0, vertLine: { color: 'rgba(255,255,255,0.18)', labelBackgroundColor: '#18181b' }, horzLine: { color: 'rgba(255,255,255,0.18)', labelBackgroundColor: '#18181b' } },
    // stały, poprawny tag języka (niektóre systemy zwracają np. „en-US@posix", który psuje Intl)
    localization: { locale: 'pl-PL' },
  };
}

function padded(times, pts) {
  const m = new Map(pts.map((p) => [p.time, p.value]));
  return times.map((t) => (m.has(t) ? { time: lt(t), value: m.get(t) } : { time: lt(t) }));
}

export function createTradingChart(els, data, levels, sr, digits = 2) {
  const LC = window.LightweightCharts;
  let showLevels = true;
  const times = data.candles.map((c) => c.time);
  const priceFormat = { type: 'price', precision: digits, minMove: 1 / 10 ** digits };

  const main = LC.createChart(els.main, baseOptions(els.main.clientHeight || 460));
  const candles = main.addCandlestickSeries({
    upColor: C.up, downColor: C.down, borderUpColor: C.up, borderDownColor: C.down,
    wickUpColor: C.up, wickDownColor: C.down, priceFormat,
    // skala obejmuje też Entry / SL / TP1 / TP2, żeby poziomy zawsze były widoczne
    autoscaleInfoProvider: (orig) => {
      const r = orig();
      if (!r || !levels || !showLevels) return r;
      const extra = [levels.entry, levels.sl, levels.tp1, levels.tp2].filter((v) => Number.isFinite(v));
      return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...extra), maxValue: Math.max(r.priceRange.maxValue, ...extra) } };
    },
  });
  candles.setData(data.candles.map((c) => ({ ...c, time: lt(c.time) })));

  const lines = {};
  const addLine = (key, pts, color, width = 1.5) => {
    lines[key] = main.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat });
    lines[key].setData(padded(times, pts));
  };
  addLine('sma_fast', data.sma_fast, C.sma1);
  addLine('sma_mid', data.sma_mid, C.sma2);
  addLine('sma_slow', data.sma_slow, C.sma3, 2);

  if (data.volume && data.volume.length) {
    const vol = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, color: 'rgba(255,255,255,0.08)', lastValueVisible: false, priceLineVisible: false });
    main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    vol.setData(data.volume.map((v) => ({ time: lt(v.time), value: v.value })));
    lines.volume = vol;
  }

  // sygnały BUY/SELL z historii (zdarzenia setupu)
  candles.setMarkers(data.markers.map((m) => ({
    time: lt(m.time),
    position: m.direction === 'LONG' ? 'belowBar' : 'aboveBar',
    color: m.direction === 'LONG' ? C.up : C.down,
    shape: m.direction === 'LONG' ? 'arrowUp' : 'arrowDown',
    text: `${m.direction === 'LONG' ? 'BUY' : 'SELL'} ${m.score}`,
  })));

  // poziomy: Entry / SL / TP1-3 oraz wsparcia/opory
  const levelSpecs = [];
  if (levels) {
    levelSpecs.push({ price: levels.entry, color: C.entry, lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: 'ENTRY' });
    levelSpecs.push({ price: levels.sl, color: C.sl, lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: 'STOP LOSS' });
    ['tp1', 'tp2', 'tp3'].forEach((k) => levelSpecs.push({ price: levels[k], color: C.tp, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: k.toUpperCase() }));
  }
  const srSpecs = [];
  if (sr) {
    for (const s of sr.support || []) srSpecs.push({ price: s.price, color: C.sr, lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: `S ×${s.touches}` });
    for (const r of sr.resistance || []) srSpecs.push({ price: r.price, color: C.sr, lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: `R ×${r.touches}` });
  }
  const drawn = { levels: [], sr: [] };
  const draw = (key, specs, on) => {
    drawn[key].forEach((l) => candles.removePriceLine(l));
    drawn[key] = on ? specs.map((sp) => candles.createPriceLine(sp)) : [];
  };
  draw('levels', levelSpecs, true);
  draw('sr', srSpecs, true);

  // RSI
  const subOpts = () => { const o = baseOptions(120); o.layout = { ...o.layout, attributionLogo: false }; o.timeScale = { visible: false }; return o; };
  const rsiChart = LC.createChart(els.rsi, subOpts());
  const rsi = rsiChart.addLineSeries({ color: '#a5a1ff', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true });
  rsi.setData(padded(times, data.rsi));
  [70, 50, 30].forEach((v) => rsi.createPriceLine({ price: v, color: v === 50 ? 'rgba(255,255,255,.14)' : 'rgba(255,255,255,.08)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));

  // MACD
  const macdChart = LC.createChart(els.macd, subOpts());
  const hist = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
  const hm = new Map(data.macd_hist.map((p) => [p.time, p.value]));
  hist.setData(times.map((t) => (hm.has(t) ? { time: lt(t), value: hm.get(t), color: hm.get(t) >= 0 ? 'rgba(61,220,151,.45)' : 'rgba(255,90,110,.45)' } : { time: lt(t) })));
  const macdLine = macdChart.addLineSeries({ color: 'rgba(244,244,245,.8)', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  macdLine.setData(padded(times, data.macd));
  const sigLine = macdChart.addLineSeries({ color: '#a5a1ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  sigLine.setData(padded(times, data.macd_signal));

  // synchronizacja osi czasu
  const charts = [main, rsiChart, macdChart];
  let syncing = false;
  charts.forEach((ch) => ch.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    charts.forEach((o) => { if (o !== ch) o.timeScale().setVisibleLogicalRange(range); });
    syncing = false;
  }));
  const n = times.length;
  main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 6 });

  return {
    toggle(key, on) {
      if (lines[key]) lines[key].applyOptions({ visible: on });
      if (key === 'levels') { showLevels = on; draw('levels', levelSpecs, on); }
      if (key === 'sr') draw('sr', srSpecs, on);
      if (key === 'markers') candles.setMarkers(on ? data.markers.map((m) => ({
        time: lt(m.time), position: m.direction === 'LONG' ? 'belowBar' : 'aboveBar',
        color: m.direction === 'LONG' ? C.up : C.down, shape: m.direction === 'LONG' ? 'arrowUp' : 'arrowDown',
        text: `${m.direction === 'LONG' ? 'BUY' : 'SELL'} ${m.score}`,
      })) : []);
    },
    destroy() { charts.forEach((c) => c.remove()); },
  };
}

export function createEquityChart(el, series, markers = []) {
  const LC = window.LightweightCharts;
  const ch = LC.createChart(el, { ...baseOptions(el.clientHeight || 280), timeScale: { timeVisible: true, borderColor: 'rgba(148,163,255,0.12)' } });
  const out = [];
  for (const s of series) {
    const line = ch.addAreaSeries({
      lineColor: s.color, topColor: s.fill || 'rgba(139,125,255,0.25)', bottomColor: 'rgba(0,0,0,0)',
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      priceFormat: { type: 'custom', formatter: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}R` },
    });
    // unikalne, rosnące znaczniki czasu (kilka transakcji może zamknąć się w tej samej świecy)
    const pts = [];
    for (const p of s.data) {
      const t = lt(p.time);
      if (pts.length && t <= pts[pts.length - 1].time) pts[pts.length - 1].value = p.value;
      else pts.push({ time: t, value: p.value });
    }
    line.setData(pts);
    out.push(line);
  }
  if (markers.length && out[0]) out[0].setMarkers(markers.map((m) => ({ ...m, time: lt(m.time) })));
  ch.timeScale().fitContent();
  return { destroy: () => ch.remove() };
}
