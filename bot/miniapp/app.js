/* TTA Yordamchi Mini App — bitta sahifali ilova (framework'siz). API: bot/webapp.py */
(function () {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  const $screen = document.getElementById('screen');
  const $tabs = document.getElementById('tabs');
  const $toast = document.getElementById('toast');
  const MONTHS = ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avg', 'sen', 'okt', 'noy', 'dek'];
  const MODE_ICON = { oddiy: 'chat', rasmiy_xat: 'letter', instagram: 'camera', meta_reklama: 'megaphone', tarjimon: 'translate', dasturchi: 'code' };
  const EFFORT_LABEL = { low: 'Low — tez va arzon', medium: 'Medium', high: 'High', xhigh: 'XHigh', max: 'Max — eng chuqur' };
  const KIND_LABEL = { docx: 'Word', xlsx: 'Excel', pptx: 'PowerPoint', pdf: 'PDF', image: 'Rasm', csv: 'CSV', md: 'Markdown', txt: 'Matn', zip: 'Arxiv', file: 'Fayl' };
  const KIND_ICON = { docx: 'fileText', xlsx: 'table', pptx: 'presentation', pdf: 'pdf', image: 'image', csv: 'table', md: 'fileText', txt: 'fileText', zip: 'zip', file: 'fileText' };

  const state = { me: null, route: 'home', param: null, cache: {}, locked: null, busy: false };
  // Bot belgisi: suhbat pufakchasi + bitiruv shapkasi (bot/assets/avatar.jpg bilan bir xil)
  const MARK = '<svg class="mark" viewBox="0 0 512 512" aria-hidden="true"><path d="M152 176H360a56 56 0 0 1 56 56v112a56 56 0 0 1-56 56H214l-64 52c-8 6-18 0-16-10l8-42h10a56 56 0 0 1-56-56V232a56 56 0 0 1 56-56z" fill="#fff" opacity=".92"/><circle cx="190" cy="288" r="24" fill="#4f46e5"/><circle cx="256" cy="288" r="24" fill="#8b5cf6"/><circle cx="322" cy="288" r="24" fill="#06b6d4"/><g transform="rotate(-11 318 150)"><path d="M262 160v46s24 22 64 22 64-22 64-22v-46z" fill="#14123a"/><path d="M326 82l126 54-126 54-126-54z" fill="#1e1b4b"/><path d="M326 136l102 14v64" stroke="#fbbf24" stroke-width="7" fill="none" stroke-linecap="round"/></g></svg>';

  // ------------------------------------------------------------ yordamchilar
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => {
    v = Number(v || 0);
    if (v === 0) return '$0';
    if (v < 0.01) return '<$0.01';
    return '$' + (v < 100 ? v.toFixed(2) : v.toFixed(0));
  };
  const size = (b) => (b < 1024 * 1024 ? Math.max(1, Math.round(b / 1024)) + ' KB' : (b / 1024 / 1024).toFixed(1) + ' MB');
  const when = (iso, withTime) => {
    if (!iso) return '';
    const d = new Date(iso.replace(' ', 'T'));
    if (isNaN(d)) return iso;
    const base = d.getDate() + '-' + MONTHS[d.getMonth()];
    const year = d.getFullYear() !== new Date().getFullYear() ? ' ' + d.getFullYear() : '';
    const time = withTime ? ', ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') : '';
    return base + year + time;
  };
  const dayKey = (iso) => (iso || '').slice(0, 10);
  const haptic = (kind) => { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(kind || 'light'); } catch (e) { /* yo'q */ } };
  const notify = (kind) => { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) { /* yo'q */ } };
  let toastTimer = null;
  function toast(text) {
    $toast.textContent = text;
    $toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $toast.classList.remove('show'), 2400);
  }
  function modelLabel(id) {
    const m = (state.me && state.me.models || []).find((x) => x.id === id);
    return m ? m.label : id;
  }
  function modeOf(key) {
    return (state.me && state.me.modes || []).find((m) => m.key === key) || { key: 'oddiy', label: 'Oddiy', emoji: '💬', hint: '' };
  }
  function modeIcon(key) { return icon(MODE_ICON[key] || 'chat'); }
  function effortLabel(e) { return e ? EFFORT_LABEL[e] || e : 'Standart'; }

  // ------------------------------------------------------------ API
  async function api(path, opts) {
    opts = opts || {};
    const headers = { Authorization: 'tma ' + (tg ? tg.initData : '') };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, { method: opts.method || 'GET', headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
    } catch (e) {
      throw new Error('Server bilan aloqa yo\'q. Internetni tekshiring.');
    }
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (res.status === 401 || res.status === 403) {
      if (!path.startsWith('api/admin')) lock(data.message || 'Kirish yopiq');
      throw new Error(data.message || 'Ruxsat yo\'q');
    }
    if (!res.ok) throw new Error(data.message || ('Xato ' + res.status));
    return data;
  }

  // ------------------------------------------------------------ Telegram
  function applyTheme() {
    const scheme = (tg && tg.colorScheme) || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = scheme;
  }
  function setupTelegram() {
    applyTheme();
    if (!tg) return;
    try {
      tg.ready();
      tg.expand();
      tg.onEvent('themeChanged', applyTheme);
      if (tg.setHeaderColor) tg.setHeaderColor('secondary_bg_color');
      if (tg.setBackgroundColor) tg.setBackgroundColor('secondary_bg_color');
      if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      tg.BackButton.onClick(() => { if (sheetOpen()) closeSheet(); else history.back(); });
    } catch (e) { /* eski mijoz */ }
  }
  function backButton(show) {
    if (!tg || !tg.BackButton) return;
    try { show ? tg.BackButton.show() : tg.BackButton.hide(); } catch (e) { /* yo'q */ }
  }

  // ------------------------------------------------------------ sheet (pastki panel)
  const $sheetRoot = document.getElementById('sheet-root');
  function sheetOpen() { return !!$sheetRoot.firstChild; }
  function openSheet(html, mount) {
    closeSheet(true);
    const el = document.createElement('div');
    el.className = 'sheet';
    el.innerHTML = '<div class="sheet-panel"><div class="sheet-handle"></div>' + html + '</div>';
    el.addEventListener('click', (e) => { if (e.target === el) closeSheet(); });
    $sheetRoot.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    backButton(true);
    if (mount) mount(el);
    return el;
  }
  function closeSheet(silent) {
    const el = $sheetRoot.firstChild;
    if (!el) return;
    el.remove();
    if (!silent) backButton(state.route !== 'home' && !!state.param);
  }

  // ------------------------------------------------------------ marshrut
  const TABS = [
    { id: 'home', label: 'Bosh', icon: 'home' },
    { id: 'history', label: 'Tarix', icon: 'history' },
    { id: 'files', label: 'Fayllar', icon: 'folder' },
    { id: 'stats', label: 'Xarajat', icon: 'chart' },
    { id: 'admin', label: 'Admin', icon: 'shield', owner: true },
  ];
  function renderTabs() {
    const owner = state.me && state.me.user.is_owner;
    $tabs.innerHTML = TABS.filter((t) => !t.owner || owner).map((t) =>
      '<button class="tab' + (state.route === t.id ? ' active' : '') + '" data-tab="' + t.id + '" aria-label="' + t.label + '">' + icon(t.icon) + '<span>' + t.label + '</span></button>'
    ).join('');
  }
  $tabs.addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (!b) return;
    haptic('light');
    location.hash = '#' + b.dataset.tab;
  });
  function parseRoute() {
    const h = (location.hash || '#home').slice(1).split('/');
    state.route = h[0] || 'home';
    state.param = h[1] || null;
    if (!TABS.some((t) => t.id === state.route)) { state.route = 'home'; state.param = null; }
  }
  async function render() {
    if (state.locked) return renderLock();
    parseRoute();
    renderTabs();
    backButton(!!state.param);
    closeSheet(true);
    $screen.scrollTop = 0;
    window.scrollTo(0, 0);
    const fn = { home: renderHome, history: state.param ? renderConversation : renderHistory, files: renderFiles, stats: renderStats, admin: renderAdmin }[state.route];
    try {
      await fn();
    } catch (e) {
      if (!state.locked) $screen.innerHTML = emptyState('alert', 'Xato', e.message);
    }
  }
  window.addEventListener('hashchange', render);

  function lock(message) {
    state.locked = message;
    renderLock();
  }
  function renderLock() {
    $tabs.innerHTML = '';
    const inTelegram = tg && tg.initData;
    $screen.innerHTML = '<div class="lock">' + icon('lock') + '<h1>' + (inTelegram ? 'Kirish yopiq' : 'Faqat Telegram ichida') + '</h1><p>' +
      esc(state.locked || 'Ilovani Telegram\'dagi TTA Yordamchi botidan oching.') + '</p>' +
      (inTelegram ? '<div class="btn-row"><button class="btn secondary" data-action="close">Botga qaytish</button></div>' : '') + '</div>';
  }
  function emptyState(ic, title, text) {
    return '<div class="empty">' + icon(ic) + '<b>' + esc(title) + '</b>' + esc(text || '') + '</div>';
  }
  function skeleton(n) { return Array.from({ length: n || 4 }, () => '<div class="skeleton"></div>').join(''); }

  async function loadMe(force) {
    if (state.me && !force) return state.me;
    state.me = await api('api/me');
    renderTabs();
    return state.me;
  }

  // ------------------------------------------------------------ BOSH SAHIFA
  function heroHtml(me) {
    const s = me.settings;
    const b = me.budget;
    const pct = b.percent == null ? null : Math.min(b.percent, 100);
    const cls = pct == null ? '' : pct >= 100 ? 'bad' : pct >= b.economy_percent ? 'warn' : '';
    const model = s.model_override ? modelLabel(s.model_override) + ' (zaxira)' : modelLabel(s.model);
    const mode = modeOf(s.mode);
    return '<div class="hero">' + MARK +
      '<div class="hello">Salom, ' + esc(me.user.name) + '</div>' +
      '<div class="big">' + icon(s.model === 'auto' ? 'sparkles' : 'cpu') + esc(model) + '</div>' +
      '<div class="chips">' +
      '<span class="chip">' + icon('brain') + esc(effortLabel(s.effort).split(' ')[0]) + '</span>' +
      '<span class="chip">' + modeIcon(mode.key) + esc(mode.label) + '</span>' +
      '<span class="chip' + (s.web_search ? '' : ' off') + '">' + icon('globe') + (s.web_search ? 'Qidiruv yoqiq' : 'Qidiruv o\'chiq') + '</span>' +
      '</div>' +
      '<div class="foot"><div class="stat"><div class="k">Bugun</div><div class="v">' + money(me.spend.today) + ' <small style="font-weight:400;opacity:.8">· ' + me.spend.today_requests + ' so\'rov</small></div></div>' +
      (pct == null ? '' : '<div class="meter"><div class="k"><span>Kredit</span><span>' + Math.round(b.percent) + '%</span></div><div class="bar"><i class="' + cls + '" style="width:' + pct + '%"></i></div></div>') +
      '</div></div>';
  }
  function row(tile, ic, label, sub, right, action, extra) {
    return '<button class="row" data-action="' + action + '" ' + (extra || '') + '>' +
      '<span class="tile ' + tile + '">' + icon(ic) + '</span><span class="body"><div class="label">' + label + '</div>' +
      (sub ? '<div class="sub">' + sub + '</div>' : '') + '</span>' + (right || '') + '</button>';
  }
  function toggleRow(tile, ic, label, sub, key, on) {
    return '<label class="row"><span class="tile ' + tile + '">' + icon(ic) + '</span><span class="body"><div class="label">' + label + '</div>' +
      (sub ? '<div class="sub">' + sub + '</div>' : '') + '</span><span class="switch"><input type="checkbox" data-toggle="' + key + '"' + (on ? ' checked' : '') + '><span></span></span></label>';
  }
  const chev = () => icon('chevronRight', 'chev');

  async function renderHome() {
    $screen.innerHTML = skeleton(5);
    const me = await loadMe(true);
    const s = me.settings;
    const templates = (await api('api/templates')).items;
    state.cache.templates = templates;
    const mode = modeOf(s.mode);
    $screen.innerHTML =
      heroHtml(me) +
      '<form class="ask" data-form="ask"><textarea rows="1" name="text" placeholder="Savol yozing — javob chatda keladi" maxlength="8000"></textarea>' +
      '<button class="btn-icon" type="submit" aria-label="Yuborish">' + icon('send') + '</button></form>' +
      '<div class="section"><div class="section-h">Sozlamalar</div><div class="group">' +
      row('model', 'cpu', 'Model', esc(modelLabel(s.model) === 'Avto' ? 'savolga qarab tanlanadi' : (me.models.find((m) => m.id === s.model) || {}).note || ''), '<span class="value">' + esc(modelLabel(s.model)) + '</span>' + chev(), 'pick-model') +
      row('think', 'brain', 'Fikrlash darajasi', 'chuqurroq, lekin sekinroq', '<span class="value">' + esc(effortLabel(s.effort).split(' — ')[0]) + '</span>' + chev(), 'pick-effort') +
      row('mode', 'target', 'Ish rejimi', esc(mode.hint.split(' — ')[0]), '<span class="value">' + esc(mode.label) + '</span>' + chev(), 'pick-mode') +
      toggleRow('search', 'globe', 'Internet qidiruvi', 'yangi ma\'lumot uchun, ≈$0.01 / qidiruv', 'web_search', s.web_search) +
      toggleRow('cost', 'coins', 'Javob ostida narx', 'har javobda sarf ko\'rinadi', 'show_cost', s.show_cost) +
      '</div></div>' +
      '<div class="section"><div class="section-h">Joriy suhbat</div><div class="group">' +
      row('chat', 'chat', esc(me.conversation.title), me.conversation.turns + ' ta savol-javob', chev(), 'open-conv', 'data-conv="' + me.conversation.conv_id + '"') +
      '</div><div class="btn-row"><button class="btn secondary" data-action="new-conv">' + icon('plus') + 'Yangi suhbat</button>' +
      '<button class="btn ghost" data-action="go" data-to="history">' + icon('history') + 'Tarix</button></div></div>' +
      '<div class="section"><div class="section-h">Shablonlar <button data-action="add-template">+ Qo\'shish</button></div>' +
      (templates.length ? '<div class="group">' + templates.map((t) =>
        row('template', 'bolt', esc(t.name), esc(t.text.slice(0, 70)), chev(), 'open-template', 'data-id="' + t.id + '"')
      ).join('') + '</div>' : '<div class="group">' + emptyState('bolt', 'Shablon yo\'q', 'Tez-tez yuboriladigan so\'rovni saqlab, bir bosishda yuboring.') + '</div>') +
      '</div>' +
      '<p class="note">TTA Yordamchi v' + esc(me.version || '') + ' · Claude asosida</p>';
    const ta = $screen.querySelector('textarea');
    ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; });
  }

  async function patchSettings(fields, okText) {
    const data = await api('api/settings', { method: 'PATCH', body: fields });
    state.me.settings = data.settings;
    notify('success');
    if (okText) toast(okText);
  }

  function pickModel() {
    const me = state.me;
    openSheet('<h3 class="sheet-title">Model</h3><p class="sheet-sub">Narx: 1 mln token kirish / chiqish</p><div class="group">' +
      me.models.map((m) => '<button class="option" data-pick="model" data-value="' + m.id + '">' +
        '<span class="tile ' + (m.id === 'auto' ? 'model' : 'admin') + '">' + icon(m.id === 'auto' ? 'sparkles' : 'cpu') + '</span>' +
        '<span class="body"><div>' + esc(m.label) + '</div><div class="sub">' + esc(m.note) + (m.price_in != null ? ' · $' + m.price_in + ' / $' + m.price_out : '') + '</div></span>' +
        (me.settings.model === m.id ? icon('check', 'mark') : '') + '</button>').join('') + '</div>');
  }
  function pickEffort() {
    const me = state.me;
    const items = [['', 'Standart', 'modelga qarab: Fable — high, Opus — medium']].concat(me.efforts.map((e) => [e, EFFORT_LABEL[e].split(' — ')[0], EFFORT_LABEL[e].split(' — ')[1] || '']));
    openSheet('<h3 class="sheet-title">Fikrlash darajasi</h3><p class="sheet-sub">Yuqori daraja — chuqurroq, lekin sekin va qimmatroq</p><div class="group">' +
      items.map((it) => '<button class="option" data-pick="effort" data-value="' + it[0] + '"><span class="tile think">' + icon('brain') + '</span>' +
        '<span class="body"><div>' + esc(it[1]) + '</div>' + (it[2] ? '<div class="sub">' + esc(it[2]) + '</div>' : '') + '</span>' +
        ((me.settings.effort || '') === it[0] ? icon('check', 'mark') : '') + '</button>').join('') + '</div>');
  }
  function pickMode() {
    const me = state.me;
    openSheet('<h3 class="sheet-title">Ish rejimi</h3><p class="sheet-sub">Rejim javob uslubini belgilaydi va /new dan keyin ham saqlanadi</p><div class="group">' +
      me.modes.map((m) => '<button class="option" data-pick="mode" data-value="' + m.key + '"><span class="tile mode">' + modeIcon(m.key) + '</span>' +
        '<span class="body"><div>' + esc(m.label) + '</div><div class="sub">' + esc(m.hint) + '</div></span>' +
        ((me.settings.mode || 'oddiy') === m.key ? icon('check', 'mark') : '') + '</button>').join('') + '</div>');
  }
  function templateSheet(t) {
    openSheet('<h3 class="sheet-title">' + esc(t.name) + '</h3><div class="group"><div class="row no-tile"><span class="body" style="white-space:pre-wrap;font-size:15px">' + esc(t.text) + '</span></div></div>' +
      '<div class="btn-row"><button class="btn" data-action="send-template" data-id="' + t.id + '">' + icon('send') + 'Chatga yuborish</button>' +
      '<button class="btn danger" data-action="delete-template" data-id="' + t.id + '" style="flex:0 0 56px" aria-label="O\'chirish">' + icon('trash') + '</button></div>');
  }
  function addTemplateSheet() {
    openSheet('<h3 class="sheet-title">Yangi shablon</h3><form data-form="template"><div class="field"><label>Nomi</label><input name="name" maxlength="40" required placeholder="Masalan: Post"></div>' +
      '<div class="field"><label>So\'rov matni</label><textarea name="text" maxlength="4000" required placeholder="Shu mavzuda Instagram post yoz: "></textarea></div>' +
      '<button class="btn" type="submit">Saqlash</button></form>', (el) => el.querySelector('input').focus());
  }

  // ------------------------------------------------------------ TARIX
  let searchTimer = null;
  async function renderHistory(query) {
    const q = (query || '').trim();
    if (query === undefined) $screen.innerHTML = '<div class="title">Suhbatlar</div><div class="search-box">' + icon('search') + '<input type="search" placeholder="Qidirish…" data-search="history" value="' + esc(q) + '"></div><div id="history-list">' + skeleton(6) + '</div>';
    const data = await api('api/conversations?limit=60' + (q ? '&q=' + encodeURIComponent(q) : ''));
    const list = document.getElementById('history-list');
    if (!list) return;
    if (!data.items.length) {
      list.innerHTML = emptyState('history', q ? 'Topilmadi' : 'Hali suhbat yo\'q', q ? 'Boshqa so\'z bilan qidirib ko\'ring.' : 'Botga savol yozing — suhbatlar shu yerda saqlanadi.');
      return;
    }
    list.innerHTML = '<div class="group">' + data.items.map((c) =>
      '<button class="row no-tile" data-action="open-conv" data-conv="' + c.conv_id + '"><span class="body"><div class="label">' + esc(c.title) +
      (c.conv_id === data.current ? '<span class="badge">joriy</span>' : '') + '</div><div class="sub">' + when(c.updated_at, true) + ' · ' + c.turns + ' ta savol-javob</div></span>' + chev() + '</button>'
    ).join('') + '</div>' + (data.total > data.items.length ? '<p class="note">Jami ' + data.total + ' ta; eski suhbatlarni qidiruv orqali toping.</p>' : '');
  }
  async function renderConversation() {
    $screen.innerHTML = '<button class="back" data-action="back">' + icon('chevronLeft') + 'Tarix</button>' + skeleton(5);
    const data = await api('api/conversations/' + encodeURIComponent(state.param));
    const me = await loadMe();
    const current = me.settings.conv_id === data.conversation.conv_id;
    $screen.innerHTML = '<button class="back" data-action="back">' + icon('chevronLeft') + 'Tarix</button>' +
      '<div class="title">' + esc(data.conversation.title) + '<small>' + when(data.conversation.updated_at, true) + ' · ' + data.conversation.turns + ' ta savol-javob' + (current ? ' · joriy' : '') + '</small></div>' +
      '<div class="msgs">' + (data.messages.length ? data.messages.map((m) =>
        '<div class="msg ' + m.role + '">' + m.attachments.map((a) => '<span class="att">' + esc(a) + '</span>').join('') + (m.attachments.length && m.text ? '\n' : '') + esc(m.text) + '</div>'
      ).join('') : emptyState('chat', 'Bo\'sh suhbat', '')) + '</div>' +
      '<div class="sticky-bottom">' + (current ? '<button class="btn" data-action="close">' + icon('chat') + 'Chatda davom ettirish</button>' :
        '<button class="btn" data-action="resume" data-conv="' + data.conversation.conv_id + '">' + icon('rotate') + 'Shu suhbatni davom ettirish</button>') + '</div>';
  }

  // ------------------------------------------------------------ FAYLLAR
  async function renderFiles() {
    $screen.innerHTML = '<div class="title">Fayllar<small>Bot yaratgan hujjatlar va grafiklar</small></div>' + skeleton(5);
    const data = await api('api/files?limit=200');
    state.cache.files = data.items;
    if (!data.items.length) {
      $screen.innerHTML = '<div class="title">Fayllar</div>' + emptyState('folder', 'Hali fayl yo\'q', 'Chatda «Word qilib ber», «Excel jadval tuz» yoki «grafik chiz» deb so\'rang — fayllar shu yerda to\'planadi.');
      return;
    }
    const groups = {};
    data.items.forEach((f) => { (groups[dayKey(f.created_at)] = groups[dayKey(f.created_at)] || []).push(f); });
    $screen.innerHTML = '<div class="title">Fayllar<small>' + data.total + ' ta · bot yaratgan hujjatlar va grafiklar</small></div>' +
      Object.keys(groups).map((day) => '<div class="date-h">' + when(day + 'T00:00:00') + '</div><div class="group">' + groups[day].map((f) =>
        row(f.kind, KIND_ICON[f.kind] || 'fileText', esc(f.name), esc((KIND_LABEL[f.kind] || 'Fayl') + ' · ' + size(f.size) + (f.conv_title ? ' · ' + f.conv_title : '')), chev(), 'open-file', 'data-id="' + f.id + '"')
      ).join('') + '</div>').join('');
  }
  function fileSheet(f) {
    openSheet('<h3 class="sheet-title">' + esc(f.name) + '</h3><p class="sheet-sub">' + esc((KIND_LABEL[f.kind] || 'Fayl') + ' · ' + size(f.size) + ' · ' + when(f.created_at, true)) + '</p>' +
      '<div class="group">' +
      (f.sendable ? row('chat', 'send', 'Chatga yuborish', 'fayl Telegram chatiga qayta keladi', '', 'file-send', 'data-id="' + f.id + '"') : '') +
      (f.downloadable ? row('files', 'download', 'Yuklab olish', 'telefonga saqlash', '', 'file-download', 'data-id="' + f.id + '"') : '') +
      '<button class="row danger" data-action="file-delete" data-id="' + f.id + '"><span class="tile cost">' + icon('trash') + '</span><span class="body"><div class="label">O\'chirish</div><div class="sub">galereyadan va serverdan</div></span></button>' +
      '</div>');
  }

  // ------------------------------------------------------------ XARAJAT
  function barChart(days, width) {
    const W = Math.max(300, Math.round(width || 340)), H = 170, padL = 36, padR = 8, padT = 10, padB = 24;
    const max = Math.max(0.01, ...days.map((d) => d.cost));
    const nice = max < 0.1 ? Math.ceil(max * 100) / 100 : max < 1 ? Math.ceil(max * 10) / 10 : Math.ceil(max);
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const bw = innerW / days.length;
    const y = (v) => padT + innerH - (v / nice) * innerH;
    let svg = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="Kunlik sarf, oxirgi 30 kun">';
    [0, 0.5, 1].forEach((t) => {
      const yy = y(nice * t);
      svg += '<line class="grid" x1="' + padL + '" x2="' + (W - padR) + '" y1="' + yy + '" y2="' + yy + '"/>';
      svg += '<text class="axis" x="' + (padL - 4) + '" y="' + (yy + 3) + '" text-anchor="end">' + (t ? money(nice * t) : '0') + '</text>';
    });
    const todayIdx = days.length - 1;
    days.forEach((d, i) => {
      const x = padL + i * bw + bw * 0.2, w = bw * 0.6;
      const h = Math.max(d.cost > 0 ? 2 : 0, (d.cost / nice) * innerH);
      if (h) svg += '<rect class="b' + (i === todayIdx ? ' sel' : '') + '" data-i="' + i + '" x="' + x.toFixed(1) + '" y="' + (padT + innerH - h).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2"/>';
      svg += '<rect class="hit" data-i="' + i + '" x="' + (padL + i * bw).toFixed(1) + '" y="' + padT + '" width="' + bw.toFixed(1) + '" height="' + innerH + '"/>';
      if ((i % 5 === 0 && i < todayIdx - 4) || i === todayIdx) {
        const last = i === todayIdx;
        svg += '<text class="axis" x="' + (last ? W - padR : padL + i * bw + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="' + (last ? 'end' : 'middle') + '">' + (last ? 'bugun' : when(d.date + 'T00:00:00')) + '</text>';
      }
    });
    return svg + '</svg>';
  }
  async function renderStats() {
    $screen.innerHTML = '<div class="title">Xarajat</div>' + skeleton(5);
    const data = await api('api/stats');
    const me = await loadMe();
    const t = data.totals, b = data.budget;
    const since = when(b.cycle_start + 'T00:00:00');
    const maxModel = Math.max(0.0001, ...data.by_model.map((m) => m.cost));
    let html = '<div class="title">Xarajat<small>Botning o\'z hisobi (taxminiy). Aniq balans — platform.claude.com</small></div>' +
      '<div class="stats"><div class="stat-tile lead"><div><div class="k">Bugun</div><div class="v">' + money(t.today) + '</div><div class="s">' + t.today_requests + ' ta so\'rov</div></div><span class="tile cost">' + icon('wallet') + '</span></div>' +
      '<div class="stat-tile"><div class="k">Davr</div><div class="v">' + money(t.cycle) + '</div><div class="s">' + since + ' dan</div></div>' +
      '<div class="stat-tile"><div class="k">Jami</div><div class="v">' + money(t.all) + '</div><div class="s">' + t.all_requests + ' ta so\'rov</div></div>' +
      '<div class="stat-tile"><div class="k">Kuniga</div><div class="v">' + money(t.cycle / Math.max(1, b.days_elapsed || 1)) + '</div><div class="s">o\'rtacha</div></div></div>' +
      '<div class="section"><div class="chart-wrap"><div class="ch-title">Kunlik sarf</div><div class="ch-sub">Oxirgi 30 kun · ustunga bosing</div><div id="chart-box"></div><div class="tip" id="chart-tip">Bugun: <b>' + money(t.today) + '</b> · ' + t.today_requests + ' so\'rov</div></div></div>';
    if (data.by_model.length) {
      html += '<div class="section"><div class="section-h">Modellar bo\'yicha, ' + since + ' dan</div><div class="group">' + data.by_model.map((m) =>
        '<div class="hbar"><span class="name">' + esc(m.label) + '</span><span class="track"><i style="width:' + Math.max(2, m.cost / maxModel * 100) + '%"></i></span><span class="num">' + money(m.cost) + '</span></div>'
      ).join('') + '</div><p class="note">' + data.by_model.map((m) => esc(m.label) + ' — ' + m.count + ' ta').join(' · ') + '</p></div>';
    }
    if (data.everyone) {
      const e = data.everyone;
      const pct = b.monthly > 0 ? Math.min(e.cycle / b.monthly * 100, 100) : 0;
      const maxUser = Math.max(0.0001, ...e.by_user.map((u) => u.cost));
      html += '<div class="section"><div class="section-h">Barcha foydalanuvchilar</div><div class="group">' +
        '<div class="meter-row"><div class="k"><span>Kredit, ' + since + ' dan</span><span>' + money(e.cycle) + (b.monthly > 0 ? ' / $' + b.monthly : '') + '</span></div><div class="bar"><i style="width:' + pct + '%"></i></div></div>' +
        e.by_user.map((u) => '<div class="hbar"><span class="name">' + esc(u.name) + '</span><span class="track"><i style="width:' + Math.max(2, u.cost / maxUser * 100) + '%"></i></span><span class="num">' + money(u.cost) + '</span></div>').join('') +
        '</div><p class="note">Bugun hammasi: ' + money(e.today) + '. Kredit umumiy — ' + (me.budget.economy_percent || 0) + '% dan keyin hamma ' + esc(me.budget.economy_model) + ' bilan ishlaydi.</p></div>';
    }
    html += '<div class="section"><div class="group">' + toggleRow('cost', 'coins', 'Javob ostida narx', 'har javobda sarf ko\'rinadi', 'show_cost', data.show_cost) + '</div></div>';
    $screen.innerHTML = html;
    const box = document.getElementById('chart-box');
    box.innerHTML = barChart(data.days, box.clientWidth);
    const tip = document.getElementById('chart-tip');
    $screen.querySelector('.chart').addEventListener('click', (ev) => {
      const el = ev.target.closest('[data-i]');
      if (!el) return;
      const d = data.days[+el.dataset.i];
      $screen.querySelectorAll('.chart .b.sel').forEach((n) => n.classList.remove('sel'));
      const bar = $screen.querySelector('.chart .b[data-i="' + el.dataset.i + '"]');
      if (bar) bar.classList.add('sel');
      tip.innerHTML = when(d.date + 'T00:00:00') + ': <b>' + money(d.cost) + '</b> · ' + d.requests + ' so\'rov' + (d.searches ? ' · ' + d.searches + ' qidiruv' : '');
      haptic('light');
    });
  }

  // ------------------------------------------------------------ ADMIN
  async function renderAdmin() {
    $screen.innerHTML = '<div class="title">Admin panel</div>' + skeleton(5);
    const a = await api('api/admin');
    state.cache.admin = a;
    const b = a.budget;
    const pct = b.percent == null ? 0 : Math.min(b.percent, 100);
    const active = a.members.filter((m) => m.active), removed = a.members.filter((m) => !m.active);
    const member = (m) => '<label class="row"><span class="tile admin">' + icon('user') + '</span><span class="body"><div class="label">' + esc(m.name) + (m.username ? ' <span class="sub" style="display:inline">@' + esc(m.username) + '</span>' : '') + '</div>' +
      '<div class="sub">' + when(m.joined_at) + ' dan · davrda ' + money(m.cycle_cost) + '</div></span><span class="switch"><input type="checkbox" data-member="' + m.user_id + '"' + (m.active ? ' checked' : '') + '><span></span></span></label>';
    $screen.innerHTML = '<div class="title">Admin panel<small>Faqat bot egasi ko\'radi</small></div>' +
      '<div class="group">' +
      row('admin', 'shield', 'Versiya', a.github_repo ? 'yangilash: pastdagi GitHub qatori yoki chatda /yangilash' : 'yangilash: chatda /yangilash → .zip', '<span class="value">v' + esc(a.version || '?') + '</span>', 'none') +
      (a.github_repo ? row('admin', 'download', 'GitHub\'dan yangilash', esc(a.github_repo) + (a.update_check ? ' · har kuni tekshiriladi' : '') + ' — so\'nggi versiyani yuklab, tasdiqni chatga yuboradi', '<span class="value">yuklash</span>' + chev(), 'github') : '') +
      row('template', 'key', 'Kirish paroli', a.password_set ? 'o\'rnatilgan' : 'o\'rnatilmagan — yangi odam kira olmaydi', '<span class="value">' + (a.password_set ? 'o\'zgartirish' : 'o\'rnatish') + '</span>' + chev(), 'password') +
      '<div class="meter-row"><div class="k"><span>Kredit sarfi, ' + when(b.cycle_start ? b.cycle_start + 'T00:00:00' : '') + '</span><span>' + money(b.used_all) + (b.monthly > 0 ? ' / $' + b.monthly + ' (' + Math.round(b.percent) + '%)' : '') + '</span></div><div class="bar"><i style="width:' + pct + '%"></i></div></div>' +
      '</div>' +
      '<div class="section"><div class="section-h">Foydalanuvchilar · ' + active.length + ' ta faol</div>' +
      (a.members.length ? '<div class="group">' + active.map(member).join('') + removed.map(member).join('') + '</div><p class="note">Tumbler o\'chirilsa, odam botga kira olmaydi; qayta yoqish mumkin.</p>' :
        '<div class="group">' + emptyState('users', 'Hali hech kim kirmagan', 'Parolni o\'rnatib, kerakli odamga ayting.') + '</div>') + '</div>' +
      '<div class="section"><div class="section-h">Zaxira va versiyalar</div><div class="group">' +
      row('backup', 'database', 'Zaxira nusxa', a.backup_time ? 'baza har kuni ' + esc(a.backup_time) + ' da chatga keladi' : 'avtomatik zaxira o\'chiq', '<span class="value">hozir olish</span>' + chev(), 'backup') +
      (a.backups.length ? a.backups.map((v, i) => row('history', 'rotate', 'v' + esc(v.version), v.created_at ? when(v.created_at, true) + ' da saqlangan' : '', i === 0 ? '<span class="value">qaytish</span>' + chev() : '', i === 0 ? 'restore' : 'none')).join('') :
        row('history', 'rotate', 'Oldingi versiya yo\'q', 'zaxira birinchi /yangilash da paydo bo\'ladi', '', 'none')) +
      '</div><p class="note">Qaytish tasdig\'i chatga keladi — bot 10–30 soniyaga qayta ishga tushadi.</p></div>' +
      '<p class="note">Egalar: ' + a.owner_ids.join(', ') + ' · standart model: ' + esc(a.default_model) + '</p>';
  }
  function passwordSheet() {
    const set = state.cache.admin && state.cache.admin.password_set;
    openSheet('<h3 class="sheet-title">Kirish paroli</h3><p class="sheet-sub">Parolni bilgan odam botga yozib kiradi. Kamida 6 belgi.</p>' +
      '<form data-form="password"><div class="field"><label>Yangi parol</label><input name="password" type="text" autocomplete="off" minlength="6" maxlength="64" required></div>' +
      '<button class="btn" type="submit">Saqlash</button></form>' +
      (set ? '<div class="btn-row"><button class="btn danger" data-action="password-off">Parolni o\'chirish</button></div><p class="note">O\'chirilsa, yangi odamlar kira olmaydi; kirganlar qoladi.</p>' : ''),
      (el) => el.querySelector('input').focus());
  }

  // ------------------------------------------------------------ hodisalar
  async function guard(fn) {
    if (state.busy) return false;
    state.busy = true;
    try { await fn(); return true; } catch (e) { notify('error'); toast(e.message || 'Xato'); return false; } finally { state.busy = false; }
  }
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action],[data-pick]');
    if (!el) return;
    if (el.dataset.pick) {
      const key = el.dataset.pick, value = el.dataset.value;
      haptic('light');
      return guard(async () => {
        await patchSettings({ [key]: value || null }, key === 'model' ? 'Model: ' + modelLabel(value) : key === 'effort' ? 'Fikrlash: ' + effortLabel(value || '').split(' — ')[0] : 'Rejim: ' + modeOf(value).label);
        closeSheet();
        render();
      });
    }
    const action = el.dataset.action;
    if (action === 'none') return;
    haptic('light');
    const id = el.dataset.id;
    const actions = {
      close: () => { try { tg.close(); } catch (err) { toast('Ilovani yoping'); } },
      back: () => history.back(),
      go: () => { location.hash = '#' + el.dataset.to; },
      'pick-model': pickModel, 'pick-effort': pickEffort, 'pick-mode': pickMode,
      'open-conv': () => { location.hash = '#history/' + el.dataset.conv; },
      'new-conv': () => guard(async () => { await api('api/conversations/new', { method: 'POST', body: {} }); notify('success'); toast('Yangi suhbat boshlandi'); render(); }),
      resume: () => guard(async () => { await api('api/conversations/' + el.dataset.conv + '/resume', { method: 'POST', body: {} }); notify('success'); toast('Suhbat davom ettiriladi — chatda yozing'); setTimeout(() => { try { tg.close(); } catch (err) { /* yo'q */ } }, 600); }),
      'add-template': addTemplateSheet,
      'open-template': () => { const t = (state.cache.templates || []).find((x) => String(x.id) === id); if (t) templateSheet(t); },
      'send-template': () => guard(async () => { const t = (state.cache.templates || []).find((x) => String(x.id) === id); if (!t) return; await api('api/ask', { method: 'POST', body: { text: t.text } }); closeSheet(); notify('success'); toast('Yuborildi — javob chatda'); }),
      'delete-template': () => guard(async () => { await api('api/templates/' + id, { method: 'DELETE' }); closeSheet(); toast('Shablon o\'chirildi'); render(); }),
      'open-file': () => { const f = (state.cache.files || []).find((x) => String(x.id) === id); if (f) fileSheet(f); },
      'file-send': () => guard(async () => { await api('api/files/' + id + '/send', { method: 'POST', body: {} }); closeSheet(); notify('success'); toast('Fayl chatga yuborildi'); }),
      'file-download': () => guard(async () => {
        const link = await api('api/files/' + id + '/link');
        closeSheet();
        if (tg && tg.downloadFile) {
          try { tg.downloadFile({ url: link.url, file_name: link.name }); return; } catch (err) { /* eski mijoz */ }
        }
        if (tg && tg.openLink) tg.openLink(link.url); else window.open(link.url, '_blank');
      }),
      'file-delete': () => guard(async () => { await api('api/files/' + id, { method: 'DELETE' }); closeSheet(); toast('Fayl o\'chirildi'); render(); }),
      password: passwordSheet,
      'password-off': () => guard(async () => { await api('api/admin/password', { method: 'POST', body: { remove: true } }); closeSheet(); toast('Parol o\'chirildi'); render(); }),
      backup: () => guard(async () => { toast('Zaxira olinmoqda…'); await api('api/admin/backup', { method: 'POST', body: {} }); notify('success'); toast('Zaxira nusxa chatga yuborildi'); }),
      restore: () => guard(async () => { const r = await api('api/admin/restore', { method: 'POST', body: {} }); notify('warning'); toast('Tasdiqlash so\'rovi chatga yuborildi (v' + r.version + ')'); }),
      github: () => guard(async () => { toast('GitHub\'dan yuklanmoqda…'); const r = await api('api/admin/github', { method: 'POST', body: {} }); notify('warning'); toast('Tasdiqlash so\'rovi chatga yuborildi (v' + r.current + ' → v' + r.version + ')'); }),
    };
    if (actions[action]) actions[action]();
  });
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.toggle) {
      haptic('light');
      guard(async () => {
        await patchSettings({ [t.dataset.toggle]: t.checked }, t.dataset.toggle === 'web_search' ? (t.checked ? 'Qidiruv yoqildi' : 'Qidiruv o\'chirildi') : (t.checked ? 'Narx ko\'rsatiladi' : 'Narx yashirildi'));
        if (state.route === 'home') { const hero = $screen.querySelector('.hero'); if (hero) hero.outerHTML = heroHtml(state.me); }
      }).then((ok) => { if (!ok) t.checked = !t.checked; });
    } else if (t.dataset.member) {
      haptic('medium');
      guard(async () => {
        await api('api/admin/members/' + t.dataset.member, { method: 'POST', body: { active: t.checked } });
        toast(t.checked ? 'Kirish qaytarildi' : 'Kirish yopildi');
      }).then((ok) => { if (!ok) t.checked = !t.checked; });
    }
  });
  document.addEventListener('input', (e) => {
    if (e.target.dataset.search === 'history') {
      clearTimeout(searchTimer);
      const q = e.target.value.trim();
      searchTimer = setTimeout(() => renderHistory(q).catch((err) => toast(err.message)), 300);
    }
  });
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('[data-form]');
    if (!form) return;
    e.preventDefault();
    const kind = form.dataset.form;
    const fd = new FormData(form);
    haptic('medium');
    if (kind === 'ask') {
      const text = (fd.get('text') || '').trim();
      if (!text) return;
      const button = form.querySelector('button');
      button.disabled = true;
      guard(async () => { await api('api/ask', { method: 'POST', body: { text } }); form.reset(); form.querySelector('textarea').style.height = 'auto'; notify('success'); toast('Yuborildi — javob chatda yoziladi'); }).then(() => { button.disabled = false; });
    } else if (kind === 'template') {
      guard(async () => { await api('api/templates', { method: 'POST', body: { name: fd.get('name'), text: fd.get('text') } }); closeSheet(); toast('Shablon saqlandi'); render(); });
    } else if (kind === 'password') {
      guard(async () => { await api('api/admin/password', { method: 'POST', body: { password: fd.get('password') } }); closeSheet(); notify('success'); toast('Parol o\'rnatildi'); render(); });
    }
  });

  // ------------------------------------------------------------ boshlash
  setupTelegram();
  if (!tg || !tg.initData) {
    lock('');
  } else {
    render();
  }
})();
