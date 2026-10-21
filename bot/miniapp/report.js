/* TTA Yordamchi Mini App — «Hisobotlar» bo'limi (call center). API: bot/reports/api.py
   app.js uni window.TTAReport(App) bilan ulaydi: ekranlar, tugma amallari, formalar va tumblerlar shu yerda. */
(function () {
  'use strict';
  const DAYS = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh', 'Ya'];
  const AVATAR = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#e11d48', '#7c3aed', '#0284c7', '#c026d3'];
  const REMIND = [[0, 'O\'chiq'], [15, '15 daqiqa'], [30, '30 daqiqa'], [60, '1 soat'], [120, '2 soat']];
  // vazifa holati: [belgi, yozuv, rang]
  const TSTATE = {
    waiting: ['⏳', 'kutilmoqda', ''],
    overdue: ['🔴', 'muddati o\'tdi', 'bad'],
    asked: ['🔔', 'so\'raldi', 'warn'],
    no: ['❌', '«Yo\'q» dedi', 'warn'],
    silent: ['⚠️', 'javob yo\'q', 'bad'],
    done: ['✅', 'bajarildi', 'ok'],
    cancelled: ['🗑', 'bekor', ''],
  };
  const TFILTERS = [['open', 'Ochiq'], ['done', 'Bajarilgan'], ['cancelled', 'Bekor qilingan']];
  // arxiv bo'limlari: [tur, yozuv, faqat egasi]
  const AKINDS = [['daily', 'Kunlik'], ['weekly', 'Haftalik'], ['monthly', 'Oylik'], ['journal', 'Kundalik', true]];
  const ROLE_UI = {
    leads: ['report', 'inbox', 'lid postlari va izohlar'],
    leaders: ['leader', 'eye', 'kunlik hisobot shu yerga'],
    tasks: ['task', 'listCheck', '«#vazifa» va «Bajardingizmi?»'],
  };
  const KIND = {
    operator: { title: 'Operator qo\'shish', hint: 'Lidlarga qo\'ng\'iroq qiladigan xodim. Hisobotda alohida qatorda ko\'rinadi.' },
    excluded: { title: 'Istisno qo\'shish', hint: 'Bu odamning reaksiya va izohlari hisobotda umuman sanalmaydi (masalan, rahbarlar).' },
    leader: { title: 'Rahbar qo\'shish', hint: 'Rahbar Mini App\'ning faqat Hisobotlar bo\'limini ko\'radi, sozlamalarni o\'zgartira olmaydi.' },
  };

  window.TTAReport = function (A) {
    const { api, esc, row, chev, emptyState, skeleton, openSheet, closeSheet, toast, notify, guard, state, rerender } = A;
    const icon = window.icon;
    const $screen = document.getElementById('screen');
    const cache = {};
    const owner = () => !!(state.me && state.me.user && state.me.user.is_owner);

    const TABS = [
      { id: 'r-today', label: 'Bugun', icon: 'chart' },
      { id: 'r-tasks', label: 'Vazifalar', icon: 'listCheck' },
      { id: 'r-ops', label: 'Operatorlar', icon: 'users' },
      { id: 'r-arch', label: 'Arxiv', icon: 'archive' },
      { id: 'r-set', label: 'Sozlamalar', icon: 'sliders', owner: true },
    ];

    // ---------------------------------------------------------- yordamchilar
    function avatar(name) {
      let h = 0;
      for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
      const letter = (String(name).replace(/^@/, '').trim()[0] || '?').toUpperCase();
      return '<span class="avatar" style="background:' + AVATAR[h % AVATAR.length] + '">' + esc(letter) + '</span>';
    }
    const dm = (x) => x.slice(8, 10) + '.' + x.slice(5, 7);
    const period = (d) => dm(d.start) + ' ' + d.start.slice(11) + ' → ' + dm(d.end) + ' ' + d.end.slice(11);
    const value = (text) => '<span class="value">' + text + '</span>';
    function hbar(name, count, max, num) {
      return '<div class="hbar"><span class="name" title="' + name + '">' + name + '</span><span class="track"><i style="width:' +
        Math.max(2, count / Math.max(max, 1) * 100) + '%"></i></span><span class="num">' + (num == null ? count : num) + '</span></div>';
    }
    function statusLine(d, r) {
      const parts = d.columns.filter((c) => r.counts[c]).map((c) => '<span>' + esc(c) + ' ' + r.counts[c] + '</span>');
      return '<div class="status-line">' + (parts.length ? parts.join('') : '<span>natija yo\'q</span>') + '</div>';
    }
    function opRow(d, r, action) {
      const share = r.total ? Math.round(r.positive / r.total * 100) : 0;
      const handle = r.username && r.name !== '@' + r.username ? ' <span class="handle">@' + esc(r.username) + '</span>' : '';
      return '<button class="row op" data-action="' + (action || 'none') + '" data-key="' + esc(r.key) + '">' + avatar(r.name) +
        '<span class="body"><div class="label">' + esc(r.name) + handle + '</div>' + statusLine(d, r) +
        (r.total ? '<div class="mini-bar"><i style="width:' + share + '%"></i></div>' : '') + '</span>' +
        '<span class="total">' + r.total + '<small>lid</small></span></button>';
    }
    function noGroups(isOwner) {
      return emptyState('inbox', 'Lidlar guruhi ulanmagan', isOwner
        ? 'Botni lidlar guruhiga qo\'shing va admin qiling — bot sizdan guruh turini so\'raydi. Shundan keyin natijalar shu yerda ko\'rinadi.'
        : 'Bot egasi lidlar guruhini ulagach, natijalar shu yerda ko\'rinadi.');
    }

    // ---------------------------------------------------------- BUGUN
    async function renderToday(live) {
      $screen.innerHTML = '<div class="title">Hisobot</div>' + skeleton(5);
      const d = await api('api/report/overview' + (state.param ? '?day=' + encodeURIComponent(state.param) : ''));
      if (!live()) return;
      cache.overview = d;
      if (!d.has_groups) {
        $screen.innerHTML = '<div class="title">Hisobot</div>' + noGroups(d.role === 'owner');
        return;
      }
      const t = d.totals;
      const extraTotal = d.extras.reduce((s, r) => s + r.total, 0);
      const calls = t.total + extraTotal;
      const pos = d.statuses.find((s) => s.positive);
      const posCount = t.positive + d.extras.reduce((s, r) => s + r.positive, 0);
      const share = calls ? Math.round(posCount / calls * 100) : 0;
      const all = {};
      d.rows.concat(d.extras).forEach((r) => Object.keys(r.counts).forEach((c) => { all[c] = (all[c] || 0) + r.counts[c]; }));
      const labels = {};
      d.statuses.forEach((s) => { labels[s.emoji] = s.label; });
      labels[d.other] = 'aniqlanmagan';
      let html = '<div class="hero report">' +
        '<div class="hello">' + esc(d.label) + (d.live ? ' · ' + d.end.slice(11) + ' gacha<span class="live">jonli</span>' : ' · yakunlangan') + '</div>' +
        '<div class="big">' + calls + ' <small>ta lidga qo\'ng\'iroq</small></div>' +
        '<div class="chips">' + d.columns.filter((c) => all[c]).map((c) => '<span class="chip" title="' + esc(labels[c] || '') + '">' + esc(c) + ' <b>' + all[c] + '</b> ' + esc(labels[c] || '') + '</span>').join('') +
        (calls ? '' : '<span class="chip off">hali natija yo\'q</span>') + '</div>' +
        '<div class="foot"><div class="stat"><div class="k">Yangi lidlar</div><div class="v">' + d.leads.new + '</div></div>' +
        '<div class="stat"><div class="k">Belgilanmagan</div><div class="v">' + d.leads.unmarked + '</div></div>' +
        (pos ? '<div class="stat"><div class="k">' + esc(pos.emoji + ' ' + pos.label) + '</div><div class="v">' + share + '%</div></div>' : '') +
        '</div></div>' +
        '<div class="period-nav"><button class="pill" data-action="r-day" data-day="' + esc(d.prev) + '" aria-label="Oldingi kun">' + icon('chevronLeft') + '</button>' +
        '<span class="when">' + esc(period(d)) + '</span>' +
        '<button class="pill" ' + (d.next ? 'data-action="r-day" data-day="' + esc(d.next) + '"' : 'disabled') + ' aria-label="Keyingi kun">' + icon('chevronRight') + '</button></div>';

      if (d.tasks && d.tasks.open) {
        html += '<div class="group" style="margin-top:12px">' + row('task', 'listCheck', 'Vazifalar', d.tasks.open + ' ta ochiq' +
          (d.tasks.overdue ? ' · ' + d.tasks.overdue + ' tasi muddati o\'tgan' : ''), chev(), 'r-goto', 'data-to="r-tasks"') + '</div>';
      }
      html += '<div class="section"><div class="section-h">Operatorlar' + (d.has_operator_list ? '' : ' · ro\'yxat kiritilmagan') + '</div>';
      html += d.rows.length ? '<div class="group">' + d.rows.map((r) => opRow(d, r, 'none')).join('') + '</div>'
        : '<div class="group">' + emptyState('phone', 'Hali qo\'ng\'iroq yo\'q', 'Operatorlar lidlarga reaksiya yoki izoh qoldirgach, shu yerda ko\'rinadi.') + '</div>';
      if (!d.has_operator_list && d.role === 'owner') html += '<p class="note">Operatorlar ro\'yxati bo\'sh — reaksiya yoki izoh qoldirgan hamma sanalmoqda. Ro\'yxat: Operatorlar → Qo\'shish.</p>';
      html += '</div>';
      if (d.extras.length) {
        html += '<div class="section"><div class="section-h">Ro\'yxatda yo\'q</div><div class="group">' +
          d.extras.map((r) => opRow(d, r, d.role === 'owner' ? 'r-extra' : 'none')).join('') + '</div>' +
          (d.role === 'owner' ? '<p class="note">Bosing — operator qilish yoki hisobotdan chiqarish (istisno).</p>' : '') + '</div>';
      }
      const courses = d.courses.filter((c) => c.total);
      if (courses.length) {
        const max = Math.max(...courses.map((c) => c.total));
        html += '<div class="section"><div class="section-h">Kurslar bo\'yicha' + (pos ? ' · ' + esc(pos.emoji) + ' / jami' : '') + '</div><div class="group">' +
          courses.map((c) => hbar(esc(c.course), c.total, max, (pos ? c.positive + ' / ' : '') + c.total)).join('') + '</div></div>';
      }
      if (d.reasons.length) {
        const max = Math.max(...d.reasons.map((r) => r.count));
        html += '<div class="section"><div class="section-h">Rad etish sabablari' + (d.reasons_grouped ? '' : ' · dastlabki') + '</div><div class="group">' +
          d.reasons.map((r) => hbar(esc(r.label), r.count, max)).join('') + '</div></div>';
      }
      const facts = [row('chat', 'comment', 'Izohlar', 'AI o\'qib tahlil qiladi', value(d.comments + ' ta'), 'none')];
      if (d.callbacks) facts.push(row('history', 'phone', 'Qayta qo\'ng\'iroq', 'izohda vaqt aytilgan lidlar', value(d.callbacks + ' ta'), 'none'));
      if (d.conflicts) facts.push(row('mode', 'alert', 'Ziddiyat', 'reaksiya va izoh mos emas', value(d.conflicts + ' ta'), 'none'));
      if (d.pending) facts.push(row('think', 'clock', 'Tahlil kutilmoqda', 'bir necha daqiqada tayyor', value(d.pending + ' ta'), 'none'));
      html += '<div class="section"><div class="section-h">Izohlar</div><div class="group">' + facts.join('') + '</div></div>';
      if (d.role === 'owner' && d.live) {
        html += '<div class="btn-row"><button class="btn secondary" data-action="r-send">' + icon('send') + 'Hozirgi holatni rahbarlarga yuborish</button></div>';
      }
      if (d.conflicts) html += '<p class="note">Ziddiyatda eng so\'nggi belgi (reaksiya yoki izoh) olinadi.</p>';
      html += '<p class="note">' + (d.sent ? 'Bu davr uchun kunlik hisobot yuborilgan.' : d.live ? 'Kunlik hisobot ' + esc(d.schedule.time) + ' da (' + esc(d.schedule.days) + ') rahbarlar guruhiga keladi.'
        : 'Bu davr uchun kunlik hisobot yuborilmagan.') + '</p>';
      $screen.innerHTML = html;
    }

    // ---------------------------------------------------------- VAZIFALAR
    function taskRow(t) {
      const st = TSTATE[t.state] || TSTATE.waiting;
      const sub = [esc(t.who)];
      if (t.due_label) sub.push('⏰\u00a0' + esc(t.due_label).replace(/ /g, '\u00a0') + (t.repeat ? '\u00a0🔁' : ''));
      else if (t.repeat) sub.push('🔁');
      if (t.no_count) sub.push('«Yo\'q» ×' + t.no_count);
      return '<button class="row task" data-action="r-task" data-id="' + t.id + '">' + avatar(t.who) +
        '<span class="body"><div class="label">' + esc(t.text) + '</div><div class="sub">' + sub.join(' · ') + '</div></span>' +
        '<span class="tstate ' + st[2] + '">' + st[0] + ' ' + st[1] + '</span></button>';
    }

    async function renderTasks(live) {
      const f = cache.taskFilter || 'open';
      $screen.innerHTML = '<div class="title">Vazifalar</div>' + skeleton(5);
      const d = await api('api/report/tasks?status=' + f);
      if (!live()) return;
      cache.tasks = d;
      const isOwner = owner();
      const c = d.counts;
      let html = '<div class="title">Vazifalar<small>' + c.open + ' ta ochiq' + (c.overdue ? ' · ' + c.overdue + ' tasi muddati o\'tgan' : '') + '</small></div>';
      html += '<div class="seg" role="tablist">' + TFILTERS.map((x) => '<button role="tab" aria-selected="' + (x[0] === f) + '" class="' + (x[0] === f ? 'on' : '') +
        '" data-action="r-tfilter" data-f="' + x[0] + '">' + x[1] + '</button>').join('') + '</div>';
      html += '<div class="section"><div class="section-h">' + (f === 'open' ? 'Muddati bo\'yicha' : 'Oxirgilari') +
        (isOwner ? '<button data-action="r-task-add">+ Vazifa</button>' : '') + '</div>';
      html += '<div class="group">' + (d.items.length ? d.items.map(taskRow).join('') : emptyState('listCheck',
        f === 'open' ? 'Ochiq vazifa yo\'q' : 'Hali yo\'q',
        f === 'open' ? 'Vazifa berish: «#vazifa @username nima qilish kerak, qachongacha» — guruhda yoki botga lichkada.' : '')) + '</div></div>';
      html += '<p class="note">' + (!d.has_group && isOwner
        ? '⚠️ «📋 Vazifalar» guruhi belgilanmagan — «Bajardingizmi?» so\'rovlari yuborilmaydi. Sozlamalar → Guruhlar.'
        : 'Muddatida bot guruhda ijrochini belgilab «Bajardingizmi?» deb so\'raydi: «Yo\'q» — 3 soatdan, javobsiz — 2 soatdan keyin yana; 3 marta javobsiz — rahbarga xabar.') + '</p>';
      $screen.innerHTML = html;
    }

    // ---------------------------------------------------------- ARXIV
    function pct(sm) {
      return sm.calls ? (sm.positive_emoji ? ' · ' + esc(sm.positive_emoji) + ' ' + sm.positive + ' (' + sm.positive_pct + '%)' : '') : '';
    }
    function periodRow(it) {
      const sm = it.summary;
      const sub = it.live ? 'joriy davr · hozircha' : sm ? '📞 ' + sm.calls + pct(sm) : 'bosing — natijalar va Excel';
      const badge = it.live ? '<span class="badge">joriy</span>' : it.sent ? '<span class="badge ok">yuborildi</span>' : '';
      return row('xlsx', 'table', esc(it.label), sub, badge + chev(), 'r-period', 'data-key="' + esc(it.key) + '"');
    }
    async function renderArchive(live) {
      const isOwner = owner();
      let k = cache.archKind || 'weekly';
      if (k === 'journal' && !isOwner) k = 'weekly';
      $screen.innerHTML = '<div class="title">Arxiv</div>' + skeleton(5);
      const d = await api('api/report/archive?kind=' + k);
      if (!live()) return;
      cache.archive = d;
      const sc = d.schedule;
      let html = '<div class="title">Arxiv<small>Hisobotlar, Excel fayllar' + (isOwner ? ' va kundalik' : '') + '</small></div>';
      html += '<div class="seg" role="tablist">' + AKINDS.filter((x) => !x[2] || isOwner).map((x) => '<button role="tab" aria-selected="' + (x[0] === k) +
        '" class="' + (x[0] === k ? 'on' : '') + '" data-action="r-akind" data-k="' + x[0] + '">' + x[1] + '</button>').join('') + '</div>';
      if (k !== 'journal' && !d.has_groups) {
        $screen.innerHTML = html + noGroups(isOwner);
        return;
      }
      let note = '';
      if (k === 'daily') {
        html += '<div class="section"><div class="section-h">Yuborilgan kunlik hisobotlar</div><div class="group">' + (d.items.length
          ? d.items.map((it) => row('report', 'chart', esc(it.label), it.sent_label ? 'yuborildi ' + esc(it.sent_label) : '', chev(), 'r-day', 'data-day="' + esc(it.key) + '"')).join('')
          : emptyState('chart', 'Hali yo\'q', 'Kunlik hisobot har kuni ' + sc.time + ' da yuboriladi — shu yerda arxivi turadi.')) + '</div></div>';
        note = 'Bosing — o\'sha kunning batafsil natijalari (operatorlar, kurslar, sabablar).';
      } else if (k === 'journal') {
        html += '<div class="section"><div class="section-h">Kundaliklar</div><div class="group">' + (d.items.length
          ? d.items.map((it) => row('journal', 'fileText', esc(it.label), it.sent_label ? 'yuborildi ' + esc(it.sent_label) : '', chev(), 'r-journal', 'data-key="' + esc(it.key) + '"')).join('')
          : emptyState('fileText', 'Hali kundalik yo\'q', sc.journal_enabled ? 'Kundalik har ish kuni ' + sc.journal_time + ' da sizga lichkada keladi.'
            : 'Kundalik o\'chiq — Sozlamalar → Kundalik va Excel.')) + '</div></div>';
        note = 'Kundalik faqat sizga ko\'rinadi: kunning raqamlari va vazifalar. Hozirgi holat — botda /kundalik.';
      } else {
        const weekly = k === 'weekly';
        const on = weekly ? sc.weekly_enabled : sc.monthly_enabled;
        html += '<div class="section"><div class="section-h">' + (weekly ? 'Haftalar' : 'Oylar') + ' · Excel</div><div class="group">' +
          d.items.map(periodRow).join('') + '</div></div>';
        note = on ? (weekly ? 'Har dushanba ' : 'Har oyning 1-sanasi ') + sc.excel_time + ' da o\'tgan ' + (weekly ? 'hafta' : 'oy') + ' Excel\'i ' +
          (d.leaders_group ? 'rahbarlar guruhiga (telefon raqamlari yashirilgan)' + (isOwner ? ' va sizga (to\'liq)' : '') : 'sizga') + ' keladi.'
          : 'Avtomatik yuborish o\'chiq' + (isOwner ? ' — Sozlamalar → Kundalik va Excel.' : '.');
      }
      $screen.innerHTML = html + '<p class="note">' + esc(note) + '</p>';
    }
    async function periodSheet(key) {
      const d = await api('api/report/archive/' + encodeURIComponent(key));
      const sm = d.summary;
      cache.period = d;
      const stat = (label, val) => '<div class="hist"><span>' + label + '</span><span>' + val + '</span></div>';
      let html = '<h3 class="sheet-title">' + esc(d.title) + '</h3><p class="sheet-sub">' + esc(d.label) + (sm.period ? ' · ' + esc(sm.period) : '') +
        (d.live ? ' · hozircha' : '') + '</p>';
      html += '<div class="group">' + stat('📞 Qo\'ng\'iroq qilingan lidlar', sm.calls) +
        (sm.positive_emoji ? stat(esc(sm.positive_emoji + ' ' + sm.positive_label), sm.positive + (sm.calls ? ' · ' + sm.positive_pct + '%' : '')) : '') +
        stat('📥 Yangi lidlar', sm.new_leads + ' · belgilanmagan ' + sm.unmarked) +
        stat('📋 Vazifalar (muddati shu davrda)', sm.tasks_due + (sm.tasks_due ? ' · bajarildi ' + sm.tasks_done : '')) + '</div>';
      if (sm.top && sm.top.length) {
        const max = Math.max(...sm.top.map((t) => t[1]));
        html += '<div class="section"><div class="section-h">Eng faol operatorlar</div><div class="group">' +
          sm.top.map((t) => hbar(esc(t[0]), t[1], max, t[1] + (sm.positive_emoji ? ' · ' + esc(sm.positive_emoji) + ' ' + t[2] : ''))).join('') + '</div></div>';
      }
      html += '<div class="btn-row"><button class="btn" data-action="r-xl-send" data-key="' + esc(d.key) + '">' + icon('send') + 'Excel\'ni chatga yuborish</button></div>';
      html += '<p class="note">' + (d.masked ? '🔒 Telefon raqamlari yashirilgan nusxa keladi.' : '🔓 Sizga to\'liq nusxa (telefon raqamlari bilan) keladi.') +
        (d.sent ? ' Avtomatik yuborilgan: ' + esc(d.sent_label) + '.' : '') + ' Varaqlar: Umumiy, Kunlar, Lidlar, Vazifalar.</p>';
      openSheet(html);
    }
    async function journalSheet(key) {
      const d = await api('api/report/archive/' + encodeURIComponent(key));
      openSheet('<h3 class="sheet-title">Kundalik</h3><p class="sheet-sub">' + esc(d.label) + '</p><div class="group"><div class="pre-text">' + esc(d.text) + '</div></div>');
    }

    // ---------------------------------------------------------- OPERATORLAR
    async function renderOperators(live) {
      $screen.innerHTML = '<div class="title">Operatorlar</div>' + skeleton(5);
      const people = await api('api/report/people');
      const d = await api('api/report/overview');
      if (!live()) return;
      cache.people = people;
      cache.overview = d;
      const isOwner = owner();
      const stats = {};
      d.rows.concat(d.extras).forEach((r) => { if (r.user_id) stats[r.user_id] = r; });
      let html = '<div class="title">Operatorlar<small>' + esc(d.label) + ' · ' + esc(period(d)) + '</small></div>';
      html += '<div class="section"><div class="section-h">Call center · ' + people.operators.length + ' ta' +
        (isOwner ? '<button data-action="r-add" data-kind="operator">+ Qo\'shish</button>' : '') + '</div>';
      if (people.operators.length) {
        html += '<div class="group">' + people.operators.map((p) => {
          const r = p.user_id ? stats[p.user_id] : null;
          const who = p.username ? '@' + esc(p.username) : 'ID ' + esc(p.user_id);
          const status = !p.seen ? 'guruhda hali ko\'rinmagan' : p.dm_ok ? 'eslatma oladi' : 'botga /start bosmagan';
          return '<button class="row op" data-action="' + (isOwner ? 'r-edit' : 'none') + '" data-id="' + p.id + '">' + avatar(p.label) +
            '<span class="body"><div class="label">' + esc(p.label) + (p.name ? ' <span class="handle">' + who + '</span>' : '') + '</div>' +
            '<div class="sub">' + status + '</div>' + (r && r.total ? statusLine(d, r) : '') + '</span>' +
            '<span class="total">' + (r ? r.total : 0) + '<small>lid</small></span></button>';
        }).join('') + '</div>';
      } else {
        html += '<div class="group">' + emptyState('users', 'Ro\'yxat bo\'sh', 'Ro\'yxat bo\'sh bo\'lsa, lidga reaksiya yoki izoh qoldirgan hamma sanaladi. Operatorlarning Telegram username\'ini qo\'shing.') + '</div>';
      }
      html += '</div>';
      if (d.extras.length) {
        html += '<div class="section"><div class="section-h">Ro\'yxatda yo\'q, lekin qo\'ng\'iroq qilgan</div><div class="group">' +
          d.extras.map((r) => opRow(d, r, isOwner ? 'r-extra' : 'none')).join('') + '</div></div>';
      }
      html += '<p class="note">Operator botga bir marta /start bossa, hech kim belgilamagan lidlar haqida eslatma oladi.</p>';
      $screen.innerHTML = html;
    }

    // ---------------------------------------------------------- SOZLAMALAR (faqat egasi)
    async function renderSettings(live) {
      $screen.innerHTML = '<div class="title">Sozlamalar</div>' + skeleton(6);
      const s = await api('api/report/settings');
      const people = await api('api/report/people');
      if (!live()) return;
      cache.settings = s;
      cache.people = people;
      const sc = s.schedule;
      const roleText = (g) => g.active && g.roles.length ? g.roles.map((r) => s.role_labels[r] || r).join(', ') : 'kuzatilmaydi';
      const needsAdmin = (g) => g.active && (g.roles.indexOf('leads') >= 0 || g.roles.indexOf('tasks') >= 0) && !g.is_admin;
      const leadersGroup = s.groups.some((g) => g.active && g.roles.indexOf('leaders') >= 0);
      let html = '<div class="title">Sozlamalar<small>Hisobotlar bo\'limi · faqat siz ko\'rasiz</small></div>';

      html += '<div class="section"><div class="section-h">Guruhlar</div><div class="group">' + (s.groups.length ? s.groups.map((g) =>
        row('group', 'users', esc(g.title || String(g.chat_id)), esc(roleText(g)) + (needsAdmin(g) ? ' · ⚠️ bot admin emas' : ''),
          chev(), 'r-group', 'data-chat="' + g.chat_id + '"')).join('') :
        emptyState('users', 'Hali guruh yo\'q', 'Botni lidlar va rahbarlar guruhiga qo\'shing — bot sizdan guruh turini so\'raydi.')) + '</div>' +
        '<p class="note">Lidlar guruhida botni admin qiling (huquqlar shart emas) — aks holda reaksiyalarni ko\'rmaydi.</p></div>';

      html += '<div class="section"><div class="section-h">Statuslar<button data-action="r-status-add">+ Qo\'shish</button></div><div class="group">' +
        s.statuses.map((st) => '<button class="row" data-action="r-status" data-id="' + st.id + '"><span class="tile emoji">' + esc(st.emoji) + '</span>' +
          '<span class="body"><div class="label">' + esc(st.label) + '</div>' + (st.positive ? '<div class="sub">asosiy natija — ulush va kurslar kesimida</div>' : '') + '</span>' + chev() + '</button>').join('') +
        '</div><p class="note">Operator lid postiga shu emojini bossa, natija shunday hisoblanadi. Izohlarni AI ham shu ro\'yxat bo\'yicha tushunadi.</p></div>';

      const remind = REMIND.find((x) => x[0] === sc.remind_minutes);
      html += '<div class="section"><div class="section-h">Kunlik hisobot</div><div class="group">' +
        A.toggleRow('report', 'calendar', 'Kunlik hisobot', leadersGroup ? 'rahbarlar guruhiga' : 'sizga lichkada', 'r-enabled', sc.enabled).replace('data-toggle=', 'data-rtoggle=') +
        row('clock', 'clock', 'Yuborish vaqti', 'davr shu vaqtda yopiladi', value(esc(sc.time)) + chev(), 'r-time') +
        row('history', 'calendar', 'Kunlar', 'dam kuni — keyingi kunga', value(esc(sc.days_label)) + chev(), 'r-days') +
        row('bell', 'bell', 'Belgilanmagan lid eslatmasi', 'operatorlarga lichkada', value(esc(remind ? remind[1] : sc.remind_minutes + ' daqiqa')) + chev(), 'r-remind') +
        row('clock', 'clock', 'Ish vaqti', 'eslatmalar shu oraliqda', value(esc(sc.work_start + '–' + sc.work_end)) + chev(), 'r-work') +
        '</div>' + (leadersGroup ? '' : '<p class="note">Rahbarlar guruhi belgilanmagan — hisobot sizga keladi. Guruhni «Guruhlar»da belgilang.</p>') +
        '<div class="btn-row"><button class="btn secondary" data-action="r-send">' + icon('send') + 'Hozirgi holatni yuborish</button></div></div>';

      const rt = (html) => html.replace('data-toggle=', 'data-rtoggle=');
      html += '<div class="section"><div class="section-h">Kundalik va Excel</div><div class="group">' +
        rt(A.toggleRow('journal', 'fileText', 'Kundalik', 'ish kunlari sizga lichkada: raqamlar va vazifalar', 'r-journal', sc.journal_enabled)) +
        row('clock', 'clock', 'Kundalik vaqti', '', value(esc(sc.journal_time)) + chev(), 'r-jtime') +
        rt(A.toggleRow('xlsx', 'table', 'Haftalik Excel', 'dushanba — o\'tgan hafta', 'r-weekly', sc.weekly_enabled)) +
        rt(A.toggleRow('xlsx', 'table', 'Oylik Excel', 'oyning 1-sanasi — o\'tgan oy', 'r-monthly', sc.monthly_enabled)) +
        row('clock', 'clock', 'Excel yuborish vaqti', '', value(esc(sc.excel_time)) + chev(), 'r-xtime') +
        '</div><p class="note">Excel ' + (leadersGroup ? 'rahbarlar guruhiga telefon raqamlari yashirilgan holda, sizga — to\'liq' : 'sizga (rahbarlar guruhi belgilanmagan)') +
        ' yuboriladi. Istalgan davr — Arxiv yoki botda /excel.</p></div>';

      const personRows = (list, kind, tile, ic) => list.map((p) => row(tile, ic, esc(p.label), (p.username ? '@' + esc(p.username) : 'ID ' + esc(p.user_id)) + (kind === 'leader' ? (p.seen ? ' · kirgan' : ' · hali kirmagan') : ''), chev(), 'r-edit', 'data-id="' + p.id + '"')).join('');
      html += '<div class="section"><div class="section-h">Kirish · rahbarlar<button data-action="r-add" data-kind="leader">+ Qo\'shish</button></div><div class="group">' +
        (people.leaders.length ? personRows(people.leaders, 'leader', 'leader', 'eye') : emptyState('eye', 'Rahbar qo\'shilmagan', 'Rahbarlar Mini App\'ning faqat Hisobotlar bo\'limini ko\'radi.')) + '</div></div>';
      html += '<div class="section"><div class="section-h">Istisnolar<button data-action="r-add" data-kind="excluded">+ Qo\'shish</button></div><div class="group">' +
        (people.excluded.length ? personRows(people.excluded, 'excluded', 'ban', 'ban') : emptyState('ban', 'Istisno yo\'q', 'Bu ro\'yxatdagilarning izoh va reaksiyalari hisobotda sanalmaydi.')) + '</div></div>';

      const last = s.last_report ? s.last_report.sent_at.replace('T', ' ').slice(0, 16) + ' (UTC)' : 'hali yo\'q';
      html += '<div class="section"><div class="section-h">Holat</div><div class="group">' +
        row('report', 'inbox', 'Oxirgi 24 soat', s.diag.leads + ' ta lid · ' + s.diag.reactions + ' ta reaksiya · ' + s.diag.comments + ' ta izoh', '', 'none') +
        row('bell', 'bell', 'Eslatma oladigan operatorlar', 'botga /start bosganlar', value(s.operators_dm + ' / ' + s.operators), 'none') +
        row('model', 'cpu', 'Izohlarni tahlil qiluvchi model', 'xarajat «Xarajat»da alohida', value(esc(s.model)), 'none') +
        row('history', 'send', 'Oxirgi kunlik hisobot', esc(last), '', 'none') +
        row('chat', 'external', '«📊 Batafsil» tugmasi', 'hisobot ostida, shu bo\'limni ochadi', value(s.bot.has_main_web_app ? 'ulangan' : 'o\'chiq'), 'none') +
        '</div>' + (s.bot.has_main_web_app ? '' : '<p class="note">«📊 Batafsil» tugmasini yoqish: @BotFather → botingiz → Bot Settings → Configure Mini App → Enable Mini App → ' +
          'shu ilova manzili.</p>') + '</div>';
      $screen.innerHTML = html;
    }

    // ---------------------------------------------------------- panellar (sheet)
    function personSheet(kind, preset) {
      const k = KIND[kind];
      openSheet('<h3 class="sheet-title">' + k.title + '</h3><p class="sheet-sub">' + k.hint + '</p>' +
        '<form data-form="r-person"><input type="hidden" name="kind" value="' + kind + '">' +
        '<div class="field"><label>Telegram username yoki ID</label><input name="who" required maxlength="64" placeholder="@username" autocomplete="off" autocapitalize="off" value="' + esc(preset && preset.who || '') + '"></div>' +
        '<div class="field"><label>Ism (hisobotda shunday ko\'rinadi)</label><input name="name" maxlength="40" placeholder="Masalan: Malika" value="' + esc(preset && preset.name || '') + '"></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>', (el) => { const i = el.querySelector(preset ? 'input[name=name]' : 'input[name=who]'); if (i) i.focus(); });
    }
    function editSheet(p) {
      const kindLabel = { operator: 'Operator', excluded: 'Istisno', leader: 'Rahbar' }[p.kind] || '';
      openSheet('<h3 class="sheet-title">' + esc(p.label) + '</h3><p class="sheet-sub">' + kindLabel + ' · ' + (p.username ? '@' + esc(p.username) : 'ID ' + esc(p.user_id)) +
        (p.seen ? ' · ID: ' + esc(p.user_id) : ' · guruhda hali ko\'rinmagan') + '</p>' +
        '<form data-form="r-rename"><input type="hidden" name="id" value="' + p.id + '"><div class="field"><label>Ism</label><input name="name" maxlength="40" value="' + esc(p.name) + '" placeholder="Ism"></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>' +
        '<div class="btn-row"><button class="btn danger" data-action="r-delete" data-id="' + p.id + '">' + icon('trash') + 'Ro\'yxatdan o\'chirish</button></div>');
    }
    function extraSheet(r) {
      openSheet('<h3 class="sheet-title">' + esc(r.name) + '</h3><p class="sheet-sub">' + (r.username ? '@' + esc(r.username) + ' · ' : '') + 'ro\'yxatda yo\'q, lekin bu davrda ' + r.total + ' ta lidga qo\'ng\'iroq qilgan</p>' +
        '<div class="group">' +
        row('report', 'userPlus', 'Operator qilish', 'hisobotda alohida qatorda ko\'rinadi', '', 'r-promote', 'data-kind="operator" data-key="' + esc(r.key) + '"') +
        row('ban', 'ban', 'Istisno qilish', 'hisobotda umuman sanalmaydi', '', 'r-promote', 'data-kind="excluded" data-key="' + esc(r.key) + '"') +
        '</div>');
    }
    function groupSheet(g) {
      const s = cache.settings;
      const toggle = (role) => {
        const ui = ROLE_UI[role];
        return '<label class="row"><span class="tile ' + ui[0] + '">' + icon(ui[1]) + '</span><span class="body"><div class="label">' + esc(s.role_labels[role] || role) + '</div>' +
          '<div class="sub">' + ui[2] + '</div></span>' +
          '<span class="switch"><input type="checkbox" data-rtoggle="r-role" data-chat="' + g.chat_id + '" data-role="' + role + '"' + (g.active && g.roles.indexOf(role) >= 0 ? ' checked' : '') + '><span></span></span></label>';
      };
      const reads = g.roles.indexOf('leads') >= 0 || g.roles.indexOf('tasks') >= 0;
      openSheet('<h3 class="sheet-title">' + esc(g.title || String(g.chat_id)) + '</h3><p class="sheet-sub">' +
        (reads ? (g.is_admin ? 'Bot guruhda admin ✅' : '⚠️ Bot admin emas — reaksiya va «#vazifa» xabarlarini ko\'rmaydi') : 'Guruh turini belgilang') + '</p>' +
        '<div class="group">' + toggle('leads') + toggle('leaders') + toggle('tasks') + '</div>' +
        '<p class="note">Bot faqat «Vazifalar» guruhida «Bajardingizmi?» so\'rovlarini yozadi, qolganini jim o\'qiydi. Hammasi o\'chirilsa, guruh kuzatilmaydi.</p>');
    }
    function statusSheet(st) {
      const edit = !!st;
      openSheet('<h3 class="sheet-title">' + (edit ? esc(st.emoji) + ' ' + esc(st.label) : 'Yangi status') + '</h3>' +
        '<p class="sheet-sub">' + (edit ? 'Ma\'nosini o\'zgartiring yoki o\'chiring' : 'Operator lid postiga bosadigan emoji va uning ma\'nosi') + '</p>' +
        '<form data-form="' + (edit ? 'r-status-edit' : 'r-status-add') + '">' + (edit ? '<input type="hidden" name="id" value="' + st.id + '">' :
          '<div class="field"><label>Emoji</label><input name="emoji" required maxlength="8" placeholder="🔥" autocomplete="off"></div>') +
        '<div class="field"><label>Ma\'nosi</label><input name="label" required maxlength="30" value="' + esc(edit ? st.label : '') + '" placeholder="Masalan: keladi"></div>' +
        '<label class="row" style="padding-left:4px"><span class="body"><div class="label">Asosiy natija</div><div class="sub">ulush va kurslar kesimida</div></span>' +
        '<span class="switch"><input type="checkbox" name="positive"' + (edit && st.positive ? ' checked' : '') + '><span></span></span></label>' +
        '<button class="btn" type="submit">Saqlash</button></form>' +
        (edit ? '<div class="btn-row"><button class="btn danger" data-action="r-status-delete" data-id="' + st.id + '">' + icon('trash') + 'Statusni o\'chirish</button></div>' +
          '<p class="note">O\'chirilsa, shu emoji bo\'yicha eski natijalar «❔ aniqlanmagan» ustuniga o\'tadi.</p>' : ''));
    }
    async function taskSheet(id) {
      const t = await api('api/report/tasks/' + id);
      const isOwner = owner();
      const open = t.status === 'open';
      const st = TSTATE[t.state] || TSTATE.waiting;
      cache.task = t;
      let html = '<h3 class="sheet-title">' + esc(t.text) + '</h3><p class="sheet-sub">' + esc(t.who) +
        (t.username && t.who !== '@' + t.username ? ' · @' + esc(t.username) : '') + (t.group ? ' · «' + esc(t.group) + '»' : '') + '</p>';
      html += '<div class="group">' +
        row('clock', 'clock', 'Muddat', t.due_set ? '' : 'aytilmagan — standart qo\'yildi', value(esc(t.due_label || '—')) + (isOwner && open ? chev() : ''),
          isOwner && open ? 'r-task-due' : 'none') +
        (t.repeat ? row('history', 'history', 'Takror', '', value(esc(t.repeat_label)), 'none') : '') +
        row('bell', 'bell', 'Holat', t.no_count ? '«Yo\'q» — ' + t.no_count + ' marta' : '', '<span class="tstate ' + st[2] + '">' + st[0] + ' ' + st[1] + '</span>', 'none') +
        '</div>';
      if (t.history && t.history.length) {
        html += '<div class="section"><div class="section-h">Tarix</div><div class="group">' +
          t.history.map((h) => '<div class="hist"><span>' + esc(h.label) + '</span><span>' + esc(h.at) + '</span></div>').join('') + '</div></div>';
      }
      if (isOwner) {
        html += '<div class="btn-row">' + (open
          ? '<button class="btn" data-action="r-task-set" data-status="done">' + icon('check') + 'Bajarildi</button>' +
            '<button class="btn secondary" data-action="r-task-ask">' + icon('bell') + 'Hozir so\'rash</button>'
          : '<button class="btn secondary" data-action="r-task-set" data-status="open">' + icon('rotate') + 'Qayta ochish</button>') + '</div>';
        if (open) {
          html += '<div class="btn-row">' + (t.repeat ? '<button class="btn ghost" data-action="r-task-norepeat">🔁 Takrorni to\'xtatish</button>' : '') +
            '<button class="btn danger" data-action="r-task-set" data-status="cancelled">' + icon('trash') + 'Bekor qilish</button></div>';
        }
      }
      openSheet(html);
    }
    function taskAddSheet() {
      const d = cache.tasks || {};
      const people = (d.people || []).map((p) => '<option value="@' + esc(p.username) + '">' + esc(p.name || '') + '</option>').join('');
      openSheet('<h3 class="sheet-title">Yangi vazifa</h3><p class="sheet-sub">Muddatida guruhda ijrochidan «Bajardingizmi?» deb so\'raladi</p>' +
        '<form data-form="r-task-add">' +
        '<div class="field"><label>Kimga</label><input name="who" list="r-people" required maxlength="64" placeholder="@username" autocomplete="off" autocapitalize="off">' +
        '<datalist id="r-people">' + people + '</datalist></div>' +
        '<div class="field"><label>Vazifa</label><textarea name="text" rows="3" maxlength="500" required placeholder="Masalan: oylik hisobotni topshirish"></textarea></div>' +
        '<div class="field-row"><div class="field"><label>Muddat</label><input type="datetime-local" name="due" required value="' + esc(d.default_due || '') + '"></div>' +
        '<div class="field"><label>Takror</label><select name="repeat"><option value="">Yo\'q</option><option value="daily">Har kuni</option>' +
        '<option value="weekly">Har hafta</option><option value="monthly">Har oy</option></select></div></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>', (el) => { const i = el.querySelector('input[name=who]'); if (i) i.focus(); });
    }
    function taskDueSheet() {
      const t = cache.task;
      if (!t) return;
      openSheet('<h3 class="sheet-title">Muddat</h3><p class="sheet-sub">' + esc(t.text) + '</p>' +
        '<form data-form="r-task-due"><div class="field"><label>Yangi muddat</label><input type="datetime-local" name="due" required value="' + esc(t.due || '') + '"></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>');
    }
    function repeatValue(kind, due) {
      if (!kind || !due) return '';
      const d = new Date(due);
      if (kind === 'weekly') return 'weekly:' + ((d.getDay() + 6) % 7);
      if (kind === 'monthly') return 'monthly:' + Math.min(d.getDate(), 28);
      return kind;
    }
    async function taskPatch(body, text) {
      const t = cache.task;
      if (!t) return;
      await api('api/report/tasks/' + t.id, { method: 'PATCH', body });
      closeSheet();
      notify('success');
      toast(text);
      rerender();
    }

    function timeSheet(field, title, sub) {
      const sc = cache.settings.schedule;
      openSheet('<h3 class="sheet-title">' + title + '</h3><p class="sheet-sub">' + sub + '</p>' +
        '<form data-form="r-schedule"><div class="field"><label>Vaqt</label><input type="time" name="' + field + '" required value="' + esc(sc[field]) + '"></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>');
    }
    function daysSheet() {
      const sc = cache.settings.schedule;
      openSheet('<h3 class="sheet-title">Hisobot kunlari</h3><p class="sheet-sub">Tanlanmagan kunlar keyingi hisobotga qo\'shiladi</p>' +
        '<form data-form="r-schedule"><div class="day-chips">' + DAYS.map((name, i) => '<label><input type="checkbox" name="days" value="' + i + '"' + (sc.days.indexOf(i) >= 0 ? ' checked' : '') + '><span>' + name + '</span></label>').join('') + '</div>' +
        '<button class="btn" type="submit">Saqlash</button></form>');
    }
    function workSheet() {
      const sc = cache.settings.schedule;
      openSheet('<h3 class="sheet-title">Ish vaqti</h3><p class="sheet-sub">Belgilanmagan lid eslatmalari faqat shu oraliqda va hisobot kunlarida yuboriladi</p>' +
        '<form data-form="r-schedule"><div class="field-row"><div class="field"><label>Boshlanishi</label><input type="time" name="work_start" required value="' + esc(sc.work_start) + '"></div>' +
        '<div class="field"><label>Tugashi</label><input type="time" name="work_end" required value="' + esc(sc.work_end) + '"></div></div>' +
        '<button class="btn" type="submit">Saqlash</button></form>');
    }
    function remindSheet() {
      const sc = cache.settings.schedule;
      openSheet('<h3 class="sheet-title">Belgilanmagan lid eslatmasi</h3><p class="sheet-sub">Yangi lidni shuncha vaqt ichida hech kim belgilamasa, operatorlarga lichkada eslatma boradi</p>' +
        '<div class="group">' + REMIND.map((x) => '<button class="option" data-action="r-remind-set" data-value="' + x[0] + '"><span class="tile bell">' + icon('bell') + '</span>' +
          '<span class="body"><div>' + x[1] + '</div></span>' + (sc.remind_minutes === x[0] ? icon('check', 'mark') : '') + '</button>').join('') + '</div>');
    }

    async function saveSchedule(body, text) {
      await api('api/report/schedule', { method: 'PATCH', body });
      closeSheet();
      notify('success');
      toast(text || 'Saqlandi');
      rerender();
    }
    function findRow(key) {
      const d = cache.overview;
      return d ? d.rows.concat(d.extras).find((r) => r.key === key) : null;
    }
    function findPerson(id) {
      const p = cache.people;
      return p ? p.operators.concat(p.excluded, p.leaders).find((x) => String(x.id) === String(id)) : null;
    }

    // ---------------------------------------------------------- hodisalar
    const actions = {
      'r-day': (el) => { location.hash = '#r-today/' + el.dataset.day; },
      'r-goto': (el) => { location.hash = '#' + el.dataset.to; },
      'r-tfilter': (el) => { cache.taskFilter = el.dataset.f; rerender(); },
      'r-task': (el) => guard(() => taskSheet(el.dataset.id)),
      'r-task-add': () => taskAddSheet(),
      'r-task-due': () => taskDueSheet(),
      'r-task-set': (el) => guard(() => taskPatch({ status: el.dataset.status },
        { done: 'Vazifa yopildi', open: 'Vazifa qayta ochildi',
          cancelled: cache.task && cache.task.repeat ? 'Bekor qilindi — takror ham to\'xtadi' : 'Vazifa bekor qilindi' }[el.dataset.status] || 'Saqlandi')),
      'r-task-norepeat': () => guard(() => taskPatch({ repeat: '' }, 'Takror to\'xtatildi')),
      'r-task-ask': () => guard(async () => {
        const t = cache.task;
        if (!t) return;
        await api('api/report/tasks/' + t.id + '/ask', { method: 'POST', body: {} });
        closeSheet();
        notify('success');
        toast('So\'rov guruhga yuborildi');
        rerender();
      }),
      'r-extra': (el) => { const r = findRow(el.dataset.key); if (r) extraSheet(r); },
      'r-promote': (el) => guard(async () => {
        const r = findRow(el.dataset.key);
        if (!r) return;
        await api('api/report/people', { method: 'POST', body: { kind: el.dataset.kind, who: r.username || String(r.user_id), name: r.name.charAt(0) === '@' ? '' : r.name } });
        closeSheet();
        notify('success');
        toast(el.dataset.kind === 'operator' ? 'Operatorlar ro\'yxatiga qo\'shildi' : 'Istisnolarga qo\'shildi');
        rerender();
      }),
      'r-add': (el) => personSheet(el.dataset.kind),
      'r-edit': (el) => { const p = findPerson(el.dataset.id); if (p) editSheet(Object.assign({ kind: p.kind || kindOf(p) }, p)); },
      'r-delete': (el) => guard(async () => { await api('api/report/people/' + el.dataset.id, { method: 'DELETE' }); closeSheet(); toast('O\'chirildi'); rerender(); }),
      'r-group': (el) => { const g = (cache.settings.groups || []).find((x) => String(x.chat_id) === el.dataset.chat); if (g) groupSheet(g); },
      'r-status': (el) => { const st = cache.settings.statuses.find((x) => String(x.id) === el.dataset.id); if (st) statusSheet(st); },
      'r-status-add': () => statusSheet(null),
      'r-status-delete': (el) => guard(async () => { await api('api/report/statuses/' + el.dataset.id, { method: 'DELETE' }); closeSheet(); toast('Status o\'chirildi'); rerender(); }),
      'r-time': () => timeSheet('time', 'Yuborish vaqti', 'Hisobot davri — oldingi hisobot vaqtidan shu vaqtgacha'),
      'r-jtime': () => timeSheet('journal_time', 'Kundalik vaqti', 'Ish kunlari shu vaqtda sizga lichkada keladi'),
      'r-xtime': () => timeSheet('excel_time', 'Excel yuborish vaqti', 'Dushanba (o\'tgan hafta) va oyning 1-sanasi (o\'tgan oy)'),
      'r-days': daysSheet, 'r-work': workSheet, 'r-remind': remindSheet,
      'r-akind': (el) => { cache.archKind = el.dataset.k; rerender(); },
      'r-period': (el) => guard(() => periodSheet(el.dataset.key)),
      'r-journal': (el) => guard(() => journalSheet(el.dataset.key)),
      'r-xl-send': (el) => guard(async () => {
        toast('Tayyorlanmoqda…');
        const r = await api('api/report/archive/' + encodeURIComponent(el.dataset.key) + '/send', { method: 'POST', body: {} });
        closeSheet();
        notify('success');
        toast(r.masked ? 'Excel chatingizga yuborildi (telefonlarsiz)' : 'Excel chatingizga yuborildi');
      }),
      'r-remind-set': (el) => guard(() => saveSchedule({ remind_minutes: Number(el.dataset.value) }, 'Eslatma: ' + (REMIND.find((x) => String(x[0]) === el.dataset.value) || [0, ''])[1])),
      'r-send': () => guard(async () => {
        toast('Yuborilmoqda…');
        const r = await api('api/report/send', { method: 'POST', body: {} });
        notify('success');
        toast(r.target === 'leaders' ? 'Rahbarlar guruhiga yuborildi' : 'Sizga yuborildi (rahbarlar guruhi yo\'q yoki bot u yerdan chiqarilgan)');
      }),
    };
    function kindOf(p) {
      const c = cache.people;
      if (!c) return 'operator';
      if (c.leaders.some((x) => x.id === p.id)) return 'leader';
      if (c.excluded.some((x) => x.id === p.id)) return 'excluded';
      return 'operator';
    }
    const forms = {
      'r-task-add': (fd) => guard(async () => {
        const due = fd.get('due');
        await api('api/report/tasks', { method: 'POST', body: { who: fd.get('who'), text: fd.get('text'), due, repeat: repeatValue(fd.get('repeat'), due) } });
        closeSheet();
        notify('success');
        toast('Vazifa qo\'shildi');
        cache.taskFilter = 'open';
        rerender();
      }),
      'r-task-due': (fd) => guard(() => taskPatch({ due: fd.get('due') }, 'Muddat saqlandi')),
      'r-person': (fd) => guard(async () => {
        const kind = fd.get('kind');
        await api('api/report/people', { method: 'POST', body: { kind, who: fd.get('who'), name: fd.get('name') } });
        closeSheet();
        notify('success');
        toast({ operator: 'Operator qo\'shildi', excluded: 'Istisno qo\'shildi', leader: 'Rahbar qo\'shildi' }[kind] || 'Saqlandi');
        rerender();
      }),
      'r-rename': (fd) => guard(async () => { await api('api/report/people/' + fd.get('id'), { method: 'PATCH', body: { name: fd.get('name') } }); closeSheet(); toast('Saqlandi'); rerender(); }),
      'r-status-add': (fd) => guard(async () => {
        await api('api/report/statuses', { method: 'POST', body: { emoji: fd.get('emoji'), label: fd.get('label'), positive: fd.get('positive') === 'on' } });
        closeSheet(); notify('success'); toast('Status qo\'shildi'); rerender();
      }),
      'r-status-edit': (fd) => guard(async () => {
        await api('api/report/statuses/' + fd.get('id'), { method: 'PATCH', body: { label: fd.get('label'), positive: fd.get('positive') === 'on' } });
        closeSheet(); toast('Saqlandi'); rerender();
      }),
      'r-schedule': (fd) => guard(() => {
        const body = {};
        ['time', 'journal_time', 'excel_time'].forEach((k) => { if (fd.has(k)) body[k] = fd.get(k); });
        if (fd.has('work_start')) { body.work_start = fd.get('work_start'); body.work_end = fd.get('work_end'); }
        if (document.querySelector('.sheet [name=days]')) {
          body.days = fd.getAll('days').map(Number);
          if (!body.days.length) { toast('Kamida bitta kunni tanlang'); return Promise.resolve(); }
        }
        return saveSchedule(body);
      }),
    };
    const SWITCHES = {
      'r-journal': ['journal_enabled', 'Kundalik yoqildi', 'Kundalik o\'chirildi'],
      'r-weekly': ['weekly_enabled', 'Haftalik Excel yoqildi', 'Haftalik Excel o\'chirildi'],
      'r-monthly': ['monthly_enabled', 'Oylik Excel yoqildi', 'Oylik Excel o\'chirildi'],
    };
    function change(t) {
      const key = t.dataset.rtoggle;
      if (SWITCHES[key]) {
        const sw = SWITCHES[key];
        guard(async () => {
          await api('api/report/schedule', { method: 'PATCH', body: { [sw[0]]: t.checked } });
          cache.settings.schedule[sw[0]] = t.checked;
          toast(t.checked ? sw[1] : sw[2]);
        }).then((ok) => { if (!ok) t.checked = !t.checked; });
      } else if (key === 'r-enabled') {
        guard(async () => {
          await api('api/report/schedule', { method: 'PATCH', body: { enabled: t.checked } });
          toast(t.checked ? 'Kunlik hisobot yoqildi' : 'Kunlik hisobot o\'chirildi');
        }).then((ok) => { if (!ok) t.checked = !t.checked; });
      } else if (key === 'r-role') {
        const chat = t.dataset.chat;
        const roles = Array.from(document.querySelectorAll('.sheet [data-rtoggle="r-role"]')).filter((x) => x.checked).map((x) => x.dataset.role);
        guard(async () => {
          const r = await api('api/report/groups/' + chat, { method: 'PATCH', body: { roles, active: roles.length > 0 } });
          const g = (cache.settings.groups || []).find((x) => String(x.chat_id) === chat);
          if (g) { g.roles = r.roles; g.active = r.active; g.is_admin = r.is_admin; }
          cache.dirty = true;
          toast(roles.length ? 'Saqlandi' : 'Guruh kuzatilmaydi');
          if (r.roles.indexOf('leads') >= 0 && !r.is_admin) toast('⚠️ Botni guruhda admin qiling');
        }).then((ok) => { if (!ok) t.checked = !t.checked; });
      }
    }
    function onSheetClosed() {
      if (cache.dirty && state.route === 'r-set') { cache.dirty = false; rerender(); }
    }

    return {
      tabs: TABS, actions, forms, change, onSheetClosed,
      render: { 'r-today': renderToday, 'r-tasks': renderTasks, 'r-ops': renderOperators, 'r-arch': renderArchive, 'r-set': renderSettings },
    };
  };
})();
