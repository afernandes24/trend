// HISTORIA SYGNAŁÓW — tabela z filtrami.
import { api, esc, fmt, fmtR, fmtTime, isPro, signCls, store, toast } from '../lib.js';

export async function render(root, ctx) {
  const insts = await api('/instruments');
  const digits = Object.fromEntries(insts.map((i) => [i.symbol, i.digits]));
  const f = store.get('histFilters', {});
  root.innerHTML = `
  <div class="panel">
    <div class="panel-head"><h3>Filtry</h3>
      <div class="row"><select id="h-days" class="pro-only" style="width:auto"><option value="7">7 dni</option><option value="30" selected>30 dni</option><option value="60">60 dni</option><option value="365">365 dni</option></select>
      <button class="btn sm pro-only" id="h-backfill" title="Wylicza sygnały strategii z danych historycznych i ich wyniki">Wygeneruj historię z danych</button></div></div>
    <div class="panel-pad"><div class="form-grid">
      <div class="field"><label>Instrument</label><select id="h-sym"><option value="">Wszystkie</option>${insts.map((i) => `<option ${f.symbol === i.symbol ? 'selected' : ''}>${esc(i.symbol)}</option>`).join('')}</select></div>
      <div class="field"><label>Kierunek</label><select id="h-dir"><option value="">LONG + SHORT</option><option ${f.direction === 'LONG' ? 'selected' : ''}>LONG</option><option ${f.direction === 'SHORT' ? 'selected' : ''}>SHORT</option></select></div>
      <div class="field pro-only"><label>Min. score</label><select id="h-score"><option value="">Dowolny</option>${[65, 75, 80, 85].map((s) => `<option value="${s}" ${+f.min_score === s ? 'selected' : ''}>${s}+</option>`).join('')}</select></div>
      <div class="field"><label>Wynik</label><select id="h-res"><option value="">Wszystkie</option>${[['WIN', 'Zysk'], ['LOSS', 'Strata'], ['OPEN', 'Otwarte'], ['SL', 'SL'], ['TP1', 'TP1'], ['TP2', 'TP2'], ['TP3', 'TP3'], ['EXPIRED', 'Wygasłe']].map(([v, l]) => `<option value="${v}" ${f.result === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field pro-only"><label>Od</label><input id="h-from" type="date" value="${f.date_from || ''}"></div>
      <div class="field pro-only"><label>Do</label><input id="h-to" type="date" value="${f.date_to || ''}"></div>
      <div class="field pro-only"><label>Źródło</label><select id="h-src"><option value="">Wszystkie</option><option value="LIVE" ${f.source === 'LIVE' ? 'selected' : ''}>Na żywo (skaner)</option><option value="BACKFILL" ${f.source === 'BACKFILL' ? 'selected' : ''}>Z danych historycznych</option></select></div>
    </div></div>
  </div>
  <div class="kpis" id="h-sum" style="margin-top:16px"></div>
  <div class="panel" style="margin-top:16px"><div class="table-wrap" id="h-body"><div class="skeleton" style="height:300px;margin:16px"></div></div></div>
  <p class="disclaimer">Wynik = najwyższy Take Profit osiągnięty przed Stop Lossem (w R). Gdy SL i TP wypadają w tej samej świecy, liczony jest SL.
    Sygnał wygasa po ok. 2 dniach bez rozstrzygnięcia (wynik wg ceny zamknięcia). Sygnały „z danych historycznych" to setupy wyliczone wstecz przez tę samą strategię.</p>`;

  const $ = (id) => root.querySelector(id);
  async function load() {
    const params = {
      symbol: $('#h-sym').value, direction: $('#h-dir').value, min_score: $('#h-score').value,
      result: $('#h-res').value, date_from: $('#h-from').value, date_to: $('#h-to').value, source: $('#h-src').value,
    };
    store.set('histFilters', params);
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
    let data;
    try { data = await api(`/signals?${q}`); } catch (e) { $('#h-body').innerHTML = `<div class="errbox" style="margin:16px">${esc(e.message)}</div>`; return; }
    if (!ctx.isCurrent()) return;
    const s = data.summary;
    $('#h-sum').innerHTML = `
      <div class="panel kpi"><div class="label">Sygnały</div><div class="value">${s.count}</div><div class="sub">zamknięte: ${s.closed}</div></div>
      <div class="panel kpi"><div class="label">Win rate (zamknięte)</div><div class="value">${s.win_rate === null ? '—' : fmt(s.win_rate, 1) + '%'}</div><div class="sub">wynik historyczny</div></div>
      <div class="panel kpi"><div class="label">Suma wyników</div><div class="value ${signCls(s.total_r)}">${fmtR(s.total_r)}</div><div class="sub">w jednostkach ryzyka</div></div>`;
    if (!data.items.length) {
      $('#h-body').innerHTML = '<div class="empty">Brak sygnałów dla wybranych filtrów. Skaner zapisuje nowe setupy automatycznie — możesz też wygenerować historię z danych.</div>';
      return;
    }
    const res = (r) => {
      if (r.status === 'OPEN') return '<span class="badge info">OTWARTY</span>';
      const cls = r.result_r > 0 ? 'LONG' : r.result_r < 0 ? 'SHORT' : '';
      return `<span class="badge ${cls}">${esc(r.result)} ${fmtR(r.result_r, 1)}</span>`;
    };
    const pro = isPro();
    $('#h-body').innerHTML = `<table class="t cards"><thead><tr><th>Data</th><th>Instrument</th><th>Kierunek</th><th class="r">Entry</th>
      ${pro ? '<th class="r">SL</th><th class="r">TP1 / TP2 / TP3</th>' : ''}<th class="r">Score</th><th>Wynik</th>${pro ? '<th>Źródło</th>' : ''}<th></th></tr></thead><tbody>
      ${data.items.map((r) => {
        const d = digits[r.symbol] ?? 2;
        const canEnter = r.status === 'OPEN' && r.source === 'LIVE';
        return `<tr class="click" data-sym="${esc(r.symbol)}">
        <td data-l="Data" class="mono">${fmtTime(r.bar_ts)}</td>
        <td data-l="Instrument"><b>${esc(r.symbol)}</b> <span class="dim">${esc(r.tf)}</span></td>
        <td data-l="Kierunek" class="${r.direction === 'LONG' ? 'long' : 'short'}">${r.direction}</td>
        <td data-l="Entry" class="r mono">${fmt(r.entry, d)}</td>
        ${pro ? `<td data-l="SL" class="r mono short">${fmt(r.sl, d)}</td>
        <td data-l="TP" class="r mono">${fmt(r.tp1, d)} / ${fmt(r.tp2, d)} / ${fmt(r.tp3, d)}</td>` : ''}
        <td data-l="Score" class="r mono">${r.score}</td>
        <td data-l="Wynik">${res(r)}</td>
        ${pro ? `<td data-l="Źródło" class="muted">${r.source === 'LIVE' ? 'skaner' : 'historia'}</td>` : ''}
        <td data-l="" class="r">${canEnter ? `<a class="btn sm" data-enter href="#/positions?signal=${r.id}">Wszedłem</a>` : ''}</td></tr>`;
      }).join('')}</tbody></table>`;
    root.querySelectorAll('tr.click').forEach((tr) => { tr.onclick = (e) => { if (e.target.closest('[data-enter]')) return; location.hash = `#/analysis/${encodeURIComponent(tr.dataset.sym)}?tf=15M`; }; });
  }
  root.querySelectorAll('.form-grid select, .form-grid input').forEach((el) => el.addEventListener('change', load));
  $('#h-backfill').onclick = async (e) => {
    e.target.disabled = true;
    e.target.innerHTML = '<span class="spinner"></span> Liczenie…';
    try {
      const r = await api('/signals/backfill', { json: { days: +$('#h-days').value } });
      const n = Object.values(r.inserted).reduce((a, b) => a + b, 0);
      toast(`Wygenerowano ${n} sygnałów z danych historycznych.`);
      await load();
    } catch (err) { toast(err.message, 'error'); }
    e.target.disabled = false;
    e.target.textContent = 'Wygeneruj historię z danych';
  };
  await load();
}
