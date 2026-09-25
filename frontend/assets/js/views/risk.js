// KALKULATOR POZYCJI — używany samodzielnie oraz w widoku analizy.
import { api, esc, fmt, store } from '../lib.js';

let instrumentsCache = null;
let fxCache = null;
async function instruments() {
  if (!instrumentsCache) instrumentsCache = await api('/instruments');
  return instrumentsCache;
}
async function fx() {
  if (!fxCache) {
    try { fxCache = await api('/fx'); } catch { fxCache = { rates: { USDPLN: 3.65, EURPLN: 4.25, GBPPLN: 4.9, JPYPLN: 0.025 }, live: [] }; }
  }
  return fxCache;
}

export async function render(root) {
  root.innerHTML = '<div id="calc-host"></div>';
  await renderCalculator(root.querySelector('#calc-host'), {});
}

/**
 * prefill: { symbol, entry, sl, tp1, tp2, tp3, direction }
 */
export async function renderCalculator(host, prefill = {}) {
  const [insts, fxd] = await Promise.all([instruments(), fx()]);
  const saved = store.get('calc', { capital: 10000, risk: 1, limit: 2 });
  const sym = prefill.symbol || insts[0].symbol;
  host.innerHTML = `
  <div class="panel">
    <div class="panel-head"><h3>Kalkulator pozycji</h3><span class="badge info">TRYB ANALIZY — bez składania zleceń</span></div>
    <div class="panel-pad">
      <div class="form-grid">
        <div class="field"><label>Kapitał (PLN)</label><input id="c-cap" type="number" min="0" step="100" value="${saved.capital}"></div>
        <div class="field"><label>Ryzyko na transakcję (%)</label><input id="c-risk" type="number" min="0" step="0.1" value="${saved.risk}"></div>
        <div class="field"><label>Twój limit ryzyka (%)</label><input id="c-limit" type="number" min="0.1" step="0.1" value="${saved.limit}"></div>
        <div class="field"><label>Instrument</label><select id="c-sym">${insts.map((i) => `<option ${i.symbol === sym ? 'selected' : ''}>${esc(i.symbol)}</option>`).join('')}</select></div>
        <div class="field"><label>Kierunek</label><select id="c-dir"><option ${prefill.direction !== 'SHORT' ? 'selected' : ''}>LONG</option><option ${prefill.direction === 'SHORT' ? 'selected' : ''}>SHORT</option></select></div>
        <div class="field"><label>Entry</label><input id="c-entry" type="number" step="any" value="${prefill.entry ?? ''}"></div>
        <div class="field"><label>Stop Loss</label><input id="c-sl" type="number" step="any" value="${prefill.sl ?? ''}"></div>
        <div class="field"><label>TP1 / TP2 / TP3 (R)</label><input id="c-tpr" type="text" value="${(prefill.tp_r || [1.5, 2, 3]).join(' / ')}"></div>
        <div class="field"><label>Wartość 1 pkt / 1 lot</label><input id="c-vpp" type="number" step="any"></div>
        <div class="field"><label>Kurs waluty → PLN</label><input id="c-fx" type="number" step="any"></div>
      </div>
      <div class="muted" id="c-spec" style="font-size:11.5px;margin-top:10px"></div>
      <div id="c-out" style="margin-top:16px"></div>
    </div>
  </div>`;
  const $ = (id) => host.querySelector(id);
  let confirmed = false;

  function applyInstrument() {
    const inst = insts.find((i) => i.symbol === $('#c-sym').value);
    $('#c-vpp').value = inst.value_per_point;
    const pair = `${inst.currency}PLN`;
    $('#c-fx').value = inst.currency === 'PLN' ? 1 : (fxd.rates[pair] ?? '');
    $('#c-spec').innerHTML = `${esc(inst.symbol)}: wartość ruchu o 1.0 dla 1 lota = ${inst.value_per_point} ${inst.currency} · min. lot ${inst.min_lot} · krok ${inst.lot_step}.
      Kurs ${pair}: ${fxd.live?.includes(pair) ? 'bieżący ze źródła danych' : 'domyślny (edytuj)'}.
      <b>Zweryfikuj wartość punktu w specyfikacji instrumentu w xStation.</b>`;
  }

  function calc() {
    const inst = insts.find((i) => i.symbol === $('#c-sym').value);
    const cap = +$('#c-cap').value, riskPct = +$('#c-risk').value, limit = +$('#c-limit').value;
    const entry = +$('#c-entry').value, sl = +$('#c-sl').value;
    const vpp = +$('#c-vpp').value, rate = +$('#c-fx').value;
    const dir = $('#c-dir').value;
    const tpr = $('#c-tpr').value.split(/[\/;, ]+/).map(Number).filter((x) => x > 0);
    store.set('calc', { capital: cap, risk: riskPct, limit });
    const out = $('#c-out');
    if (!(cap > 0 && riskPct > 0 && entry > 0 && sl > 0 && vpp > 0 && rate > 0)) {
      out.innerHTML = '<div class="notebox">Uzupełnij kapitał, ryzyko, Entry i Stop Loss.</div>';
      return;
    }
    if ((dir === 'LONG' && sl >= entry) || (dir === 'SHORT' && sl <= entry)) {
      out.innerHTML = `<div class="errbox">Dla ${dir} Stop Loss musi być ${dir === 'LONG' ? 'poniżej' : 'powyżej'} Entry.</div>`;
      return;
    }
    if (riskPct > limit && !confirmed) {
      out.innerHTML = `<div class="warnbox" style="font-size:13px">
        ⚠️ Ryzyko ${fmt(riskPct, 2)}% przekracza Twój limit ${fmt(limit, 2)}% na transakcję.
        <div style="margin-top:10px" class="row"><label class="check"><input type="checkbox" id="c-ok"> Rozumiem, chcę zobaczyć wyliczenia mimo przekroczenia limitu</label></div></div>`;
      out.querySelector('#c-ok').onchange = (e) => { confirmed = e.target.checked; calc(); };
      return;
    }
    const riskAmount = cap * riskPct / 100;
    const dist = Math.abs(entry - sl);
    const lossPerLot = dist * vpp * rate;             // PLN przy SL dla 1 lota
    const rawLots = riskAmount / lossPerLot;
    const step = inst.lot_step || 0.01;
    const lots = Math.floor(rawLots / step + 1e-9) * step;
    const realRisk = lots * lossPerLot;
    const sgn = dir === 'LONG' ? 1 : -1;
    const rows = tpr.map((r, i) => {
      const tp = entry + sgn * r * dist;
      return `<tr><td>TP${i + 1} (${fmt(r, 2)}R)</td><td class="r mono">${fmt(tp, inst.digits)}</td><td class="r mono long">+${fmt(lots * r * lossPerLot, 2)} PLN</td></tr>`;
    }).join('');
    const tooSmall = lots < inst.min_lot;
    out.innerHTML = `
      ${riskPct > limit ? `<div class="warnbox" style="margin:0 0 12px">Uwaga: ryzyko powyżej Twojego limitu (${fmt(limit, 2)}%).</div>` : ''}
      <div class="metric-grid">
        <div class="metric"><div class="k">Maks. kwota ryzyka</div><div class="v">${fmt(riskAmount, 2)} PLN</div></div>
        <div class="metric"><div class="k">Odległość SL</div><div class="v">${fmt(dist, inst.digits)}</div><div class="muted" style="font-size:11px">${fmt(dist / entry * 100, 3)}% ceny</div></div>
        <div class="metric"><div class="k">Proponowany wolumen</div><div class="v ${tooSmall ? 'short' : 'info'}">${fmt(lots, 2)} lota</div><div class="muted" style="font-size:11px">dokładnie: ${fmt(rawLots, 4)}</div></div>
        <div class="metric"><div class="k">Realne ryzyko</div><div class="v short">−${fmt(realRisk, 2)} PLN</div><div class="muted" style="font-size:11px">${fmt(realRisk / cap * 100, 2)}% kapitału</div></div>
      </div>
      ${tooSmall ? `<div class="warnbox">Wyliczony wolumen jest mniejszy niż minimalny lot (${inst.min_lot}). Przy minimalnym locie ryzyko wyniesie ${fmt(inst.min_lot * lossPerLot, 2)} PLN (${fmt(inst.min_lot * lossPerLot / cap * 100, 2)}%).</div>` : ''}
      <div class="table-wrap" style="margin-top:14px"><table class="t"><thead><tr><th>Cel</th><th class="r">Cena</th><th class="r">Potencjalny wynik</th></tr></thead>
        <tbody>${rows}<tr><td>Stop Loss</td><td class="r mono">${fmt(sl, inst.digits)}</td><td class="r mono short">−${fmt(realRisk, 2)} PLN</td></tr></tbody></table></div>
      <p class="disclaimer">Wyliczenia nie uwzględniają spreadu, swapów ani poślizgu. Wartość punktu i kurs walut zweryfikuj w xStation przed transakcją.</p>`;
  }

  $('#c-sym').onchange = () => { applyInstrument(); calc(); };
  host.querySelectorAll('input, select').forEach((el) => el.addEventListener('input', () => {
    if (el.id === 'c-risk' || el.id === 'c-limit') confirmed = false;
    calc();
  }));
  applyInstrument();
  calc();
}
