// MOJE POZYCJE — dziennik transakcji z alertów + licznik zysków i strat.
import { api, esc, fmt, fmtTime, qs, signCls, toast } from '../lib.js';

const pln = (v, sign = true) => (v === null || v === undefined ? '—' : `${sign && v > 0 ? '+' : ''}${fmt(v, 2)} PLN`);

let instCache = null;
let fxCache = null;
async function meta() {
  if (!instCache) instCache = await api('/instruments');
  if (!fxCache) { try { fxCache = (await api('/fx')).rates; } catch { fxCache = {}; } }
  return { insts: instCache, fx: fxCache };
}

export async function render(root, ctx) {
  const { insts, fx } = await meta();
  const bySym = Object.fromEntries(insts.map((i) => [i.symbol, i]));
  root.innerHTML = `
    <div class="row between" style="margin-bottom:18px">
      <div class="muted" style="font-size:13px">Zapisz pozycję tutaj albo odpowiedz na alert w Telegramie, np. <span class="mono">0.01 4251.7</span>.</div>
      <button class="btn primary" id="p-add">+ Dodaj pozycję</button>
    </div>
    <div class="kpis" id="p-kpis"><div class="panel kpi skeleton" style="height:84px"></div></div>
    <h2 class="section-title">Otwarte pozycje</h2>
    <div id="p-open"></div>
    <h2 class="section-title">Zamknięte</h2>
    <div class="panel"><div class="table-wrap" id="p-closed"></div></div>
    <div id="p-modal"></div>`;
  const $ = (s) => root.querySelector(s);

  async function load() {
    let d;
    try { d = await api('/positions'); } catch (e) { $('#p-open').innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
    if (!ctx.isCurrent()) return;
    const s = d.stats;
    $('#p-kpis').innerHTML = `
      <div class="panel kpi"><div class="label">Wynik łącznie</div><div class="value ${signCls(s.total_pln)}">${pln(s.total_pln)}</div><div class="sub">${s.trades} transakcji${s.win_rate !== null ? ` · ${fmt(s.win_rate, 0)}% na plus` : ''}</div></div>
      <div class="panel kpi"><div class="label">Ten miesiąc</div><div class="value ${signCls(s.month_pln)}">${pln(s.month_pln)}</div><div class="sub">tydzień: ${pln(s.week_pln)}</div></div>
      <div class="panel kpi"><div class="label">Dziś</div><div class="value ${signCls(s.today_pln)}">${pln(s.today_pln)}</div><div class="sub">zamknięte dzisiaj</div></div>
      <div class="panel kpi"><div class="label">Otwarte teraz</div><div class="value ${signCls(s.open_est_pln)}">${pln(s.open_est_pln)}</div><div class="sub">${s.open_count} poz. · ryzyko ${pln(-s.open_risk_pln, false)}</div></div>`;

    $('#p-open').innerHTML = d.open.length ? `<div class="tiles">${d.open.map(openCard).join('')}</div>`
      : '<div class="panel empty">Brak otwartych pozycji. Gdy wejdziesz w transakcję z alertu, kliknij „+ Dodaj pozycję”.</div>';
    $('#p-closed').innerHTML = d.closed.length ? `<table class="t cards"><thead><tr>
        <th>Zamknięta</th><th>Instrument</th><th>Kierunek</th><th class="r">Wejście → wyjście</th><th class="r">Lot</th><th class="r">Wynik</th><th class="r">R</th><th></th></tr></thead><tbody>
        ${d.closed.map((p) => `<tr>
          <td data-l="Zamknięta" class="mono">${fmtTime(p.closed_at)}</td>
          <td data-l="Instrument"><b>${esc(p.symbol)}</b></td>
          <td data-l="Kierunek" class="${p.direction === 'LONG' ? 'long' : 'short'}">${p.direction}</td>
          <td data-l="Wejście → wyjście" class="r mono">${fmt(p.entry, p.digits)} → ${p.exit_price !== null ? fmt(p.exit_price, p.digits) : '<span class="dim">z XTB</span>'}</td>
          <td data-l="Lot" class="r mono">${fmt(p.lots, 2)}</td>
          <td data-l="Wynik" class="r mono ${signCls(p.pnl_pln)}" style="font-weight:600">${pln(p.pnl_pln)}</td>
          <td data-l="R" class="r mono muted">${p.r_multiple !== null ? fmt(p.r_multiple, 2) : '—'}</td>
          <td data-l="" class="r"><button class="btn sm" data-reopen="${p.id}" title="Cofnij zamknięcie">↺</button> <button class="btn sm" data-del="${p.id}" title="Usuń">✕</button></td>
        </tr>`).join('')}</tbody></table>` : '<div class="empty">Tu pojawią się zamknięte transakcje z wynikiem.</div>';

    root.querySelectorAll('[data-close]').forEach((b) => { b.onclick = () => closeModal(d.open.find((p) => p.id === +b.dataset.close)); });
    root.querySelectorAll('[data-del]').forEach((b) => {
      b.onclick = async () => {
        if (!confirm('Usunąć tę pozycję z dziennika?')) return;
        try { await api(`/positions/${b.dataset.del}`, { method: 'DELETE' }); load(); } catch (e) { toast(e.message, 'error'); }
      };
    });
    root.querySelectorAll('[data-reopen]').forEach((b) => {
      b.onclick = async () => { try { await api(`/positions/${b.dataset.reopen}/reopen`, { method: 'POST' }); load(); } catch (e) { toast(e.message, 'error'); } };
    });
  }

  function openCard(p) {
    const d = p.digits;
    const hit = p.probably_hit
      ? `<div class="warnbox">Cena prawdopodobnie doszła do ${p.probably_hit === 'TP' ? 'Take Profit' : 'Stop Loss'} — sprawdź w XTB i zamknij tutaj.</div>` : '';
    return `<div class="panel tile sig-${p.direction}" style="cursor:default">
      <div class="tile-top"><div><div class="sym">${esc(p.symbol)}</div><div class="sym-name">${fmt(p.lots, 2)} lota · od ${fmtTime(p.created_at)}</div></div>
        <span class="badge ${p.direction}">${p.direction}</span></div>
      <div class="price-row"><span class="price ${signCls(p.est_pnl_pln)}">${pln(p.est_pnl_pln)}</span><span class="muted" style="font-size:11px">teraz, szacunkowo</span></div>
      <div class="levels" style="margin:0 0 6px">
        <span class="lk">Wejście</span><span class="lv">${fmt(p.entry, d)}</span>
        <span class="lk">Stop Loss</span><span class="lv">${fmt(p.sl, d)} <span class="short">${p.risk_pln !== null ? pln(-p.risk_pln, false) : ''}</span></span>
        <span class="lk">Take Profit</span><span class="lv">${fmt(p.tp, d)} <span class="long">${p.target_pln !== null ? pln(p.target_pln) : ''}</span></span>
      </div>
      ${hit}
      <div class="row" style="margin-top:12px;gap:8px"><button class="btn sm primary" data-close="${p.id}">Zamknij pozycję</button><button class="btn sm" data-del="${p.id}">Usuń</button></div>
    </div>`;
  }

  // ------------------------------------------------------------ modal helpers
  function modal(html) {
    $('#p-modal').innerHTML = `<div class="modal-bg"><div class="panel modal fade-in">${html}</div></div>`;
    const bg = root.querySelector('.modal-bg');
    bg.onclick = (e) => { if (e.target === bg) $('#p-modal').innerHTML = ''; };
    return bg;
  }

  function closeModal(p) {
    const m = modal(`
      <div class="section-title" style="margin-top:0">Zamknij ${esc(p.symbol)} ${p.direction}</div>
      <div class="form-grid" style="grid-template-columns:1fr 1fr">
        <div class="field"><label>Cena zamknięcia (XTB)</label><input id="c-exit" type="number" step="any" placeholder="${fmt(p.est_price ?? p.entry, p.digits).replace(/\s/g, '')}"></div>
        <div class="field"><label>albo wynik z XTB (PLN)</label><input id="c-pnl" type="number" step="any" placeholder="np. -42.50"></div>
      </div>
      <div class="row" style="gap:8px;margin-top:10px">
        ${p.tp ? `<button class="btn sm" id="c-tp">Doszło do TP</button>` : ''}${p.sl ? `<button class="btn sm" id="c-sl">Doszło do SL</button>` : ''}
      </div>
      <div id="c-prev" class="muted" style="margin-top:12px;font-size:13px"></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px;gap:8px"><button class="btn" id="c-cancel">Anuluj</button><button class="btn primary" id="c-ok">Zapisz wynik</button></div>`);
    const inst = bySym[p.symbol];
    const rate = inst.currency === 'PLN' ? 1 : (fx[`${inst.currency}PLN`] || 1);
    const sgn = p.direction === 'LONG' ? 1 : -1;
    const prev = () => {
      const x = +m.querySelector('#c-exit').value;
      const manual = m.querySelector('#c-pnl').value;
      const v = manual !== '' ? +manual : (x ? (x - p.entry) * sgn * p.lots * inst.value_per_point * rate : null);
      m.querySelector('#c-prev').innerHTML = v === null ? 'Wpisz cenę zamknięcia albo wynik w PLN (dokładny wynik widzisz w historii XTB).'
        : `Wynik: <b class="${signCls(v)}">${pln(v)}</b>${manual === '' ? ' <span class="dim">(bez spreadu i swapów)</span>' : ''}`;
    };
    m.querySelectorAll('input').forEach((i) => i.addEventListener('input', prev));
    if (m.querySelector('#c-tp')) m.querySelector('#c-tp').onclick = () => { m.querySelector('#c-exit').value = p.tp; m.querySelector('#c-pnl').value = ''; prev(); };
    if (m.querySelector('#c-sl')) m.querySelector('#c-sl').onclick = () => { m.querySelector('#c-exit').value = p.sl; m.querySelector('#c-pnl').value = ''; prev(); };
    m.querySelector('#c-cancel').onclick = () => { $('#p-modal').innerHTML = ''; };
    m.querySelector('#c-ok').onclick = async () => {
      const exit = m.querySelector('#c-exit').value; const man = m.querySelector('#c-pnl').value;
      try {
        await api(`/positions/${p.id}/close`, { json: { exit_price: exit === '' ? null : +exit, pnl_pln: man === '' ? null : +man } });
        $('#p-modal').innerHTML = ''; toast('Pozycja zamknięta.'); load();
      } catch (e) { toast(e.message, 'error'); }
    };
    prev();
  }

  async function addModal(pre = {}) {
    // wybór alertu wysłanego na Telegram (ostatnie 2 dni) — SL/TP i lot uzupełnią się z alertu
    let recent = [];
    if (!pre.signal_id) {
      try { recent = (await api('/alerts/sent?days=2')).items.filter((r) => !r.position); } catch { /* */ }
    }
    const sym = pre.symbol || recent[0]?.symbol || 'US100';
    // proponowany wolumen z ustawień alertów (kapitał × ryzyko%), gdy znamy odległość SL
    if (!pre.suggested_lots && pre.sl_distance && bySym[sym]) {
      try {
        const { settings: cfg } = await api('/settings/alerts');
        const inst = bySym[sym];
        const rate = inst.currency === 'PLN' ? 1 : (fx[`${inst.currency}PLN`] || 1);
        const raw = (cfg.capital * cfg.risk_pct / 100) / (pre.sl_distance * inst.value_per_point * rate);
        pre.suggested_lots = Math.max(inst.min_lot, Math.floor(raw / inst.lot_step + 1e-9) * inst.lot_step).toFixed(2);
      } catch { /* bez propozycji */ }
    }
    const m = modal(`
      <div class="section-title" style="margin-top:0">Nowa pozycja${pre.signal_id ? ` <span class="muted">(z alertu #${pre.signal_id})</span>` : ''}</div>
      ${!pre.signal_id && recent.length ? `<div class="field" style="margin-bottom:12px"><label>Z alertu</label><select id="n-alert">
        <option value="">— wybierz alert —</option>
        ${recent.map((r) => `<option value="${r.id}">${esc(r.symbol)} ${r.direction} · ${fmtTime(r.alert_ts)} · Entry ${fmt(r.entry, r.digits)}${r.status === 'OPEN' ? '' : ' (zamknięty)'}</option>`).join('')}
        <option value="manual">bez alertu (ręcznie)</option></select></div>` : ''}
      <div class="form-grid" style="grid-template-columns:1fr 1fr">
        <div class="field"><label>Instrument</label><select id="n-sym">${insts.map((i) => `<option ${i.symbol === sym ? 'selected' : ''}>${esc(i.symbol)}</option>`).join('')}</select></div>
        <div class="field"><label>Kierunek</label><select id="n-dir"><option ${pre.direction !== 'SHORT' ? 'selected' : ''}>LONG</option><option ${pre.direction === 'SHORT' ? 'selected' : ''}>SHORT</option></select></div>
        <div class="field"><label>Cena wejścia (z XTB)</label><input id="n-entry" type="number" step="any" autofocus></div>
        <div class="field"><label>Wolumen (loty)</label><input id="n-lots" type="number" step="0.01" min="0.01" value="${pre.suggested_lots ?? ''}"></div>
        <div class="field"><label>Stop Loss</label><input id="n-sl" type="number" step="any"></div>
        <div class="field"><label>Take Profit</label><input id="n-tp" type="number" step="any"></div>
      </div>
      ${pre.sl_distance ? `<div class="notebox" style="margin-top:12px">Z alertu: SL ${fmt(pre.sl_distance, pre.digits)} pkt, TP2 ${fmt(pre.tp_distances[1], pre.digits)} pkt od wejścia — po wpisaniu ceny wejścia SL i TP uzupełnią się same.</div>` : ''}
      <div id="n-prev" class="muted" style="margin-top:12px;font-size:13px"></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px;gap:8px"><button class="btn" id="n-cancel">Anuluj</button><button class="btn primary" id="n-ok">Zapisz pozycję</button></div>`);
    const q = (s) => m.querySelector(s);
    if (q('#n-alert')) {
      q('#n-alert').onchange = async (e) => {
        const v = e.target.value;
        if (!v || v === 'manual') return;
        try { addModal(await api(`/positions/prefill?signal=${v}`)); } catch (err) { toast(err.message, 'error'); }
      };
    }
    let touched = { sl: false, tp: false };
    q('#n-sl').addEventListener('input', () => { touched.sl = true; });
    q('#n-tp').addEventListener('input', () => { touched.tp = true; });
    const preview = () => {
      const inst = bySym[q('#n-sym').value];
      const rate = inst.currency === 'PLN' ? 1 : (fx[`${inst.currency}PLN`] || 1);
      const e = +q('#n-entry').value, lots = +q('#n-lots').value;
      const sgn = q('#n-dir').value === 'LONG' ? 1 : -1;
      if (pre.sl_distance && e && q('#n-sym').value === pre.symbol) {
        if (!touched.sl) q('#n-sl').value = +(e - sgn * pre.sl_distance).toFixed(inst.digits);
        if (!touched.tp) q('#n-tp').value = +(e + sgn * pre.tp_distances[1]).toFixed(inst.digits);
      }
      const sl2 = +q('#n-sl').value, tp2 = +q('#n-tp').value;
      if (!(e && lots)) { q('#n-prev').textContent = 'Wpisz cenę wejścia i wolumen.'; return; }
      const parts = [];
      if (sl2) parts.push(`Ryzyko: <b class="short">${pln(-Math.abs(e - sl2) * lots * inst.value_per_point * rate, false)}</b>`);
      if (tp2) parts.push(`Cel: <b class="long">${pln(Math.abs(tp2 - e) * lots * inst.value_per_point * rate)}</b>`);
      if (sl2 && tp2) parts.push(`RR 1:${fmt(Math.abs(tp2 - e) / Math.abs(e - sl2), 2)}`);
      q('#n-prev').innerHTML = parts.join(' · ') || '—';
    };
    m.querySelectorAll('input, select').forEach((el) => el.addEventListener('input', preview));
    q('#n-cancel').onclick = () => { $('#p-modal').innerHTML = ''; };
    q('#n-ok').onclick = async () => {
      const body = { symbol: q('#n-sym').value, direction: q('#n-dir').value, entry: q('#n-entry').value, lots: q('#n-lots').value,
        sl: q('#n-sl').value || null, tp: q('#n-tp').value || null, signal_id: pre.signal_id || null };
      try { await api('/positions', { json: body }); $('#p-modal').innerHTML = ''; toast('Pozycja zapisana.'); history.replaceState(null, '', '#/positions'); load(); } catch (e) { toast(e.message, 'error'); }
    };
    preview();
    q('#n-entry').focus();
  }

  $('#p-add').onclick = () => addModal();
  await load();

  // otwarcie formularza z linku w alercie / z przycisku „Wszedłem”
  const p = qs();
  if (p.signal) {
    try { addModal(await api(`/positions/prefill?signal=${encodeURIComponent(p.signal)}`)); } catch (e) { toast(e.message, 'error'); }
  } else if (p.symbol) {
    addModal({ symbol: p.symbol, direction: p.dir, sl_distance: +p.sl_d || null, tp_distances: [0, +p.tp_d || 0, 0], suggested_lots: p.lots ? +p.lots : null, digits: bySym[p.symbol]?.digits ?? 2 });
  }
  const t = setInterval(() => { if (!root.querySelector('.modal-bg')) load(); }, 30000);
  return () => clearInterval(t);
}
