// ALERTY — ustawienia powiadomień, status kanałów, test, dziennik.
import { api, esc, fmtTime, store, toast } from '../lib.js';

export async function render(root, ctx) {
  const data = await api('/settings/alerts');
  const s = data.settings;
  const ch = data.channels;
  const cal = data.calendar || {};
  const fmtEv = (e) => `${esc(e.currency)} · ${esc(e.title)} · ${fmtTime(e.time)}`;
  const chStatus = (c, name) => c.configured
    ? `<span class="badge LONG">skonfigurowany</span>`
    : `<span class="badge warn">brak konfiguracji</span> <span class="muted" style="font-size:12px">ustaw zmienne: <span class="mono">${esc(c.missing.join(', '))}</span></span>`;
  root.innerHTML = `
  <div class="two" style="align-items:start">
    <div class="panel">
      <div class="panel-head"><h3>Ustawienia alertów</h3><button class="btn sm primary" id="al-save">Zapisz</button></div>
      <div class="panel-pad stack" style="gap:18px">
        <div><div class="flabel" style="margin-bottom:8px">Kanały</div><div class="checks">
          <label class="check"><input type="checkbox" id="al-tg" ${s.telegram ? 'checked' : ''}> Telegram</label>
          <label class="check pro-only"><input type="checkbox" id="al-email" ${s.email ? 'checked' : ''}> Email</label>
          <label class="check pro-only"><input type="checkbox" id="al-sms" ${s.sms ? 'checked' : ''}> SMS</label>
          <label class="check"><input type="checkbox" id="al-browser" ${store.get('browserNotify', false) ? 'checked' : ''}> Powiadomienia w przeglądarce</label>
        </div></div>
        <div><div class="flabel" style="margin-bottom:8px">Minimalny score alertu</div><div class="checks">
          ${[75, 80, 85].map((v) => `<label class="check"><input type="radio" name="al-min" value="${v}" ${s.min_score === v ? 'checked' : ''}> tylko setupy ${v}+</label>`).join('')}
        </div></div>
        <div><div class="flabel" style="margin-bottom:8px">Kierunek</div><div class="checks">
          <label class="check"><input type="checkbox" id="al-long" ${s.long ? 'checked' : ''}> LONG</label>
          <label class="check"><input type="checkbox" id="al-short" ${s.short ? 'checked' : ''}> SHORT</label>
        </div></div>
        <div><div class="flabel" style="margin-bottom:8px">Warunki</div><div class="checks">
          <label class="check"><input type="checkbox" id="al-mtf" ${s.require_mtf ? 'checked' : ''}> tylko gdy 4H i 1H zgodne z kierunkiem</label>
        </div></div>
        <div class="form-grid">
          <div class="field"><label>Cooldown (minuty)</label><input type="number" id="al-cool" min="0" step="5" value="${s.cooldown_minutes}"></div>
        </div>
        <div class="muted" style="font-size:12px">Cooldown blokuje kolejne alerty dla tego samego instrumentu i kierunku przez podany czas. Alerty dotyczą setupów z interwału 15M.</div>
      </div>
    </div>
    <div class="stack">
      <div class="panel panel-pad">
        <div class="section-title" style="margin-top:0">Status kanałów</div>
        <div class="stack" style="gap:12px">
          <div class="row between"><div><b>Telegram</b> <span class="muted">(najszybszy)</span><div style="margin-top:4px">${chStatus(ch.telegram)}</div></div>
            <div class="row" style="gap:6px"><button class="btn sm" id="al-chats">Znajdź chat ID</button><button class="btn sm" data-test="telegram">Wyślij test</button></div></div>
          <div id="al-chat-out"></div>
          <div class="row between pro-only"><div><b>Email (SMTP)</b><div style="margin-top:4px">${chStatus(ch.email)}</div></div><button class="btn sm" data-test="email">Wyślij test</button></div>
          <div class="row between pro-only"><div><b>SMS</b> <span class="muted">(${esc(ch.sms.provider)})</span><div style="margin-top:4px">${chStatus(ch.sms)}</div></div><button class="btn sm" data-test="sms">Wyślij test</button></div>
        </div>
        <div class="notebox pro-only" style="margin-top:14px">Tokeny i dane dostępowe (Telegram, SMTP, SMS) ustawiasz w <b>Variables</b> na Railway (lokalnie: plik <span class="mono">.env</span>) — nie są zapisywane w bazie ani widoczne w przeglądarce. Po zmianie zmiennych aplikacja uruchamia się ponownie.</div>
      </div>
    </div>
  </div>
  <div class="panel" style="margin-top:16px">
    <div class="panel-head"><h3>Wolumen · filtry · raporty</h3><span class="muted" style="font-size:12px">zapisujesz przyciskiem „Zapisz” u góry</span></div>
    <div class="panel-pad three" style="gap:28px">
      <div>
        <div class="flabel" style="margin-bottom:10px">Wolumen w alercie</div>
        <div class="form-grid" style="grid-template-columns:1fr 1fr">
          <div class="field"><label>Kapitał (PLN)</label><input type="number" id="al-cap" min="0" step="100" value="${s.capital}"></div>
          <div class="field"><label>Ryzyko / transakcję (%)</label><input type="number" id="al-risk" min="0.1" max="5" step="0.1" value="${s.risk_pct}"></div>
        </div>
        <div class="muted" style="font-size:12px;margin-top:10px">Alert poda liczbę lotów, przy której strata na SL ≈ ryzyko. Wartość punktu zweryfikuj w xStation.</div>
      </div>
      <div class="stack" style="gap:14px">
        <div class="flabel">Filtry alertów</div>
        <label class="check"><input type="checkbox" id="al-stale" ${s.stale_filter ? 'checked' : ''}> Pomijaj spóźnione alerty</label>
        <div class="field" style="max-width:220px"><label>…gdy cena odjechała od Entry o więcej niż (R)</label><input type="number" id="al-stale-r" min="0.1" step="0.1" value="${s.stale_r}"></div>
        <label class="check"><input type="checkbox" id="al-quiet" ${s.quiet_enabled ? 'checked' : ''}> Ciche godziny (czas polski)</label>
        <div class="form-grid" style="grid-template-columns:1fr 1fr;max-width:260px">
          <div class="field"><label>od</label><input type="time" id="al-qf" value="${esc(s.quiet_from)}"></div>
          <div class="field"><label>do</label><input type="time" id="al-qt" value="${esc(s.quiet_to)}"></div>
        </div>
        <label class="check"><input type="checkbox" id="al-news" ${s.news_filter ? 'checked' : ''}> Bez alertów przy ważnych danych makro</label>
        <div class="field" style="max-width:220px"><label>okno ± (minuty)</label><input type="number" id="al-news-min" min="5" max="240" step="5" value="${s.news_window_minutes}"></div>
        <div class="muted" style="font-size:12px">${cal.ok
          ? `Kalendarz makro: ${cal.events_this_week} ważnych publikacji w tym tygodniu.${(cal.upcoming || []).length ? '<br>Najbliższe:<br>' + cal.upcoming.slice(0, 3).map(fmtEv).join('<br>') : ''}`
          : `<span class="warn">${esc(cal.error || 'Kalendarz makro zostanie pobrany przy pierwszym alercie.')}</span>`}</div>
      </div>
      <div class="stack" style="gap:14px">
        <div class="flabel">Raporty na Telegram</div>
        <label class="check"><input type="checkbox" id="al-res" ${s.result_notifications ? 'checked' : ''}> Wynik alertu (TP / SL)</label>
        <label class="check"><input type="checkbox" id="al-week" ${s.weekly_summary ? 'checked' : ''}> Podsumowanie tygodnia (piątek 23:00)</label>
        <div><button class="btn sm" id="al-week-now">Wyślij podsumowanie teraz</button></div>
      </div>
    </div>
  </div>
  <div class="panel" style="margin-top:16px"><div class="panel-head"><h3>Dziennik alertów</h3></div><div class="table-wrap" id="al-log"></div></div>`;

  const $ = (id) => root.querySelector(id);
  $('#al-save').onclick = async () => {
    const body = {
      telegram: $('#al-tg').checked, email: $('#al-email').checked, sms: $('#al-sms').checked,
      min_score: +(root.querySelector('input[name="al-min"]:checked')?.value ?? s.min_score),
      long: $('#al-long').checked, short: $('#al-short').checked,
      require_mtf: $('#al-mtf').checked, cooldown_minutes: +$('#al-cool').value,
      capital: +$('#al-cap').value, risk_pct: +$('#al-risk').value,
      stale_filter: $('#al-stale').checked, stale_r: +$('#al-stale-r').value,
      quiet_enabled: $('#al-quiet').checked, quiet_from: $('#al-qf').value || '23:00', quiet_to: $('#al-qt').value || '07:00',
      news_filter: $('#al-news').checked, news_window_minutes: +$('#al-news-min').value,
      result_notifications: $('#al-res').checked, weekly_summary: $('#al-week').checked,
    };
    try { await api('/settings/alerts', { method: 'PUT', json: body }); toast('Ustawienia alertów zapisane.'); } catch (e) { toast(e.message, 'error'); }
  };
  $('#al-browser').onchange = async (e) => {
    if (e.target.checked && 'Notification' in window && Notification.permission !== 'granted') {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { e.target.checked = false; toast('Przeglądarka nie zezwoliła na powiadomienia.', 'error'); }
    }
    store.set('browserNotify', e.target.checked);
  };
  $('#al-week-now').onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api('/alerts/weekly-summary', { method: 'POST' });
      const sent = (r?.sent || []).filter((x) => x.status === 'info').length;
      toast(sent ? 'Podsumowanie wysłane.' : 'Brak skonfigurowanego kanału (Telegram/e-mail) — podsumowanie nie zostało wysłane.', sent ? '' : 'error');
    } catch (err) { toast(err.message, 'error'); }
    e.target.disabled = false;
  };
  $('#al-chats').onclick = async () => {
    const out = $('#al-chat-out');
    try {
      const { chats } = await api('/alerts/telegram/chats');
      out.innerHTML = chats.length
        ? `<div class="notebox">Wpisz w <span class="mono">TELEGRAM_CHAT_ID</span>: ${chats.map((c) => `<b class="mono">${esc(String(c.chat_id))}</b> <span class="muted">(${esc(c.name || c.type)})</span>`).join(', ')}</div>`
        : '<div class="notebox">Brak wiadomości. Otwórz swojego bota w Telegramie, wyślij mu „/start" i kliknij ponownie.</div>';
    } catch (e) { out.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
  };
  root.querySelectorAll('[data-test]').forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await api('/alerts/test', { json: { channel: b.dataset.test } });
        toast(r.ok ? 'Wiadomość testowa wysłana.' : `Nie wysłano: ${r.error}`, r.ok ? '' : 'error', 9000);
      } catch (e) { toast(e.message, 'error'); }
      b.disabled = false;
    };
  });

  async function loadLog() {
    const { items } = await api('/alerts/log?limit=100');
    if (!ctx.isCurrent()) return;
    $('#al-log').innerHTML = items.length ? `<table class="t cards"><thead><tr><th>Czas</th><th>Instrument</th><th>Kierunek</th><th>Kanał</th><th>Status</th><th>Szczegóły</th></tr></thead><tbody>
      ${items.map((a) => `<tr><td data-l="Czas" class="mono">${fmtTime(a.ts)}</td><td data-l="Instrument"><b>${esc(a.symbol || '')}</b></td>
        <td data-l="Kierunek" class="${a.direction === 'LONG' ? 'long' : 'short'}">${esc(a.direction || '')}</td><td data-l="Kanał">${esc(a.channel)}</td>
        <td data-l="Status"><span class="badge ${a.status === 'sent' ? 'LONG' : a.status === 'failed' ? 'SHORT' : a.status === 'info' ? 'info' : ''}">${esc({ sent: 'wysłany', skipped: 'pominięty', failed: 'błąd', info: 'raport' }[a.status] || a.status)}</span></td>
        <td data-l="Szczegóły" class="muted" style="white-space:normal">${esc(a.error || '')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">Brak alertów. Pojawią się, gdy skaner wykryje nowy setup.</div>';
  }
  await loadLog();
  const t = setInterval(loadLog, 30000);
  return () => clearInterval(t);
}
