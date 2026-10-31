/* Henna Studio PC - home page and Admin page, as on the Pi (user 2026-10-24: "it should have same home page and
   admin page like pis studio"). Ported from the raspberry-pi branch's webui/pi_touch.js (its home / Admin / jog part;
   the Pi branch itself is not merged): the same layout and the same Studio routes and checks. Left out here: the
   storage tab and the servo angles (Pi hardware), the Pause button in the bottom bar (the Run step has its own),
   drag-to-scroll, the small-screen confirm box and the full-screen region editor. Added by webui_server_pc.py.
   Admin routes: /api/admin/* on this PC (the Pi has its own). */
/* Home and Admin (Pi 2026-10-16, user: "all the calibration, machine selection and valve selection option should
   come under admin panel", 4-digit PIN; on the PC 2026-10-24).
   - Home: chips (machine, paste) and big tiles - New henna job, Saved jobs, Daily check, Admin.
   - Admin (PIN; webui_server_pc.py refuses admin routes without the session token): one page with tabs - Machine,
     Calibrate, Daily check, Paste (dispenser + delays, test), PIN (change, lock) - see below.
     Forgotten PIN: admin_pin_reset.py on this PC.
   - Program step: shows the Admin's paste setup; the server applies it to every program.
   The same Studio routes and checks as before - nothing here moves the machine on its own. */
/* Home page after every Studio start (user 2026-10-24: "i have restarted studio multiple time but this check comes" -
   the page reopened the last job, with its finished steps ticked). Once per Studio start (window.STUDIO_BOOT, set by
   webui_server_pc.py) the remembered job is forgotten, so the page starts on the home page; the job itself is kept
   (Saved jobs / Recent jobs). Reloading the page (F5) during the same start still reopens it. Runs before the page's
   start code reads the remembered job (that waits for the machine list first). */
(function () {
  'use strict';
  try {
    if (window.STUDIO_BOOT && localStorage.getItem('studio-boot') !== window.STUDIO_BOOT) {
      localStorage.removeItem('studio-job');
      localStorage.setItem('studio-boot', window.STUDIO_BOOT);
    }
  } catch (e) { /* no storage: the page decides as before */ }
})();

(function () {
  'use strict';
  var ADMIN_TOKEN = null, SETUP = null, DISP = null;
  var ADMIN_RE = /^\/api\/(calibration\/|machines\/(select|add|recalibrate|setup_area|setup_scan)$|admin\/(paste_setup|scan_position)$)/;
  function $id(id) { return document.getElementById(id); }
  function esc2(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  /* every admin request carries the session token */
  var realFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    try {
      var path = new URL(String(url), location.href).pathname;
      if (ADMIN_TOKEN && opts && opts.method === 'POST' && (ADMIN_RE.test(path) ||
          (path === '/api/machine/jog' && document.body.classList.contains('pi-admin')))) {   /* sideways jog in Admin */
        var h = {};
        Object.keys(opts.headers || {}).forEach(function (k) { h[k] = opts.headers[k]; });
        h['X-Admin-Token'] = ADMIN_TOKEN;
        opts = Object.assign({}, opts, { headers: h });
      }
    } catch (e) { /* not a URL we know */ }
    return realFetch(url, opts);
  };
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) {
        return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
      });
  }
  function getj(url) { return fetch(url).then(function (r) { return r.json(); }); }
  function loadSetup() {
    return getj('/api/admin/paste_setup').then(function (j) { SETUP = j.setup; DISP = j.dispensers; applySetup(); renderChips(); return j; })
      .catch(function () {});
  }

  /* ---------- home ---------- */
  function buildHome() {
    var w = $id('v-welcome');
    if (!w || $id('pihome')) return;
    var h = document.createElement('div');
    h.id = 'pihome';
    h.innerHTML = '<h1 class="pih">Henna Studio</h1><div class="pichips" id="pichips"></div>' +
      '<div class="pitiles">' +
      '<button type="button" class="pitile big" id="pinew"><b>New henna job</b><span>Scan, design, run</span></button>' +
      '<button type="button" class="pitile" id="pijobs"><b>Saved jobs</b><span>Continue a job</span></button>' +
      '<button type="button" class="pitile" id="pidaily"><b>Daily check</b><span>Camera and machine check</span></button>' +
      '<button type="button" class="pitile" id="piadminbtn"><b>Admin</b><span>Machine, calibration, paste</span></button>' +
      '</div>';
    w.insertBefore(h, w.firstChild);
    $id('pinew').onclick = function () { $id('btnnew').click(); };
    $id('pijobs').onclick = function () { $id('btnjobs').click(); };
    $id('piadminbtn').onclick = openAdmin;
    $id('pidaily').onclick = showDaily;                     /* operators run it; the reference stays under Admin */
    var top = $id('btnjobs');
    if (top && !$id('pihomebtn')) {
      var b = document.createElement('button');
      b.className = 'btn small'; b.id = 'pihomebtn'; b.type = 'button'; b.textContent = 'Main menu';
      b.title = 'Back to the main menu (Home in the machine bar homes the machine)';
      b.onclick = function () {
        if (typeof machineBusy === 'function' && machineBusy()) return;
        go('welcome');                                     /* leaves Admin / daily mode (go, wrapped below) */
      };
      top.parentNode.insertBefore(b, top);
    }
  }
  function chip(kind, text) { return '<span class="chip c-' + kind + '">' + esc2(text) + '</span>'; }
  function renderChips() {
    var c = $id('pichips');
    if (!c) return;
    var m = (typeof MACH !== 'undefined') ? MACH : null, out = '';
    if (!m || !m.connected) out += chip('warn', 'Machine off');
    else if (!m.homed) out += chip('warn', 'Not homed');
    else out += chip('ok', 'Homed - ' + (m.state || ''));
    if (!SETUP) out += chip('warn', 'Paste: not set (Admin)');
    else out += chip(SETUP.dispenser === 'none' ? 'info' : 'ok', 'Paste: ' + (SETUP.dispenser === 'none' ? 'none' : SETUP.label));
    c.innerHTML = out;
  }

  function tick() {
    renderChips();
    if (inPage()) showPos();
  }

  /* ---------- Program step: the Admin's paste setup ---------- */
  function applySetup() {
    var sel = $id('pgdisp');
    if (!sel) return;
    var line = $id('pipasteline');
    if (!line) {
      line = document.createElement('div');
      line.id = 'pipasteline'; line.className = 'hint';
      sel.parentNode.insertBefore(line, sel);
    }
    ['pgdisp', 'pgdelays', 'pgdisphint', 'pgtest', 'msgpgtest'].forEach(function (id) { var e = $id(id); if (e) e.classList.add('pi-hide'); });
    var lab = document.querySelector('label[for="pgdisp"]');
    if (lab) lab.classList.add('pi-hide');
    var s = SETUP;
    if (!s || s.dispenser === 'none') {
      sel.value = 'none';
      line.textContent = s ? 'Paste: none - hover only (set in Admin).' : 'Paste: not set - choose the dispenser in Admin, Paste.';
    } else {
      sel.value = s.dispenser;
      if ($id('pgstart')) $id('pgstart').value = s.start_s;
      if ($id('pgstop')) $id('pgstop').value = s.stop_s;
      line.textContent = 'Paste: ' + s.label + ', start ' + s.start_s + ' s, stop ' + s.stop_s + ' s (set in Admin).';
    }
    if (typeof feedNote === 'function') { try { feedNote(); } catch (e) { /* program view not open */ } }
  }
  if (typeof showProgram === 'function') {
    var origShowProgram = showProgram;
    showProgram = async function () { await origShowProgram.apply(this, arguments); applySetup(); };
  }

  /* ---------- Admin: the PIN in a small box, then ONE Admin page (2026-10-18, user: "first fix these rules as they
     are so clustered into each other. fix all the rules and tick marks and required position and admin panel should
     have all options in one rather than doing it by going in other options").
     Studio's Calibration view, rearranged: tabs Machine / Calibrate / Daily check / Paste / PIN, and on top a
     Position card - the machine's state and X Y Z, what the current step needs, ONE safety tick and its own Home,
     Move to scan position (the calibration route, allowed in calibration mode) and Jog - so nothing needs a job's
     Scan step or the Daily check card any more. The one tick stands for that tab's separate safety boxes (Studio's
     own boxes stay, hidden, and are set from it); it clears when the tab changes or the page is left. The three
     approval boxes become one. The same routes and the same server checks as before.
     Daily check tile (operators, no PIN): the same page with only the Position card and the daily check. */
  var dlg = null, page = null, TAB = 'machine';
  var TABS = [['machine', 'Machine'], ['calibrate', 'Calibrate'], ['daily', 'Daily check'],
              ['paste', 'Paste'], ['pin', 'PIN']];
  var SAFE = {                       /* the one tick's words on each tab, and the Studio boxes it stands for */
    machine: ['No hand, prosthetic or person under the machine, and I am at the emergency stop.', ['calclear', 'calestop']],
    calibrate: ['Only the dot sheet or the block is on the bed - no hand, no prosthetic, no person - and I am at the ' +
                'emergency stop.', ['ctargets', 'calclear', 'calestop']],
    daily: ['The bed is empty and set up as at the reference - no hand, no person - and I am at the emergency stop.',
            ['calclear', 'calestop', 'calempty']]
  };
  var ALL_SAFE = ['ctargets', 'calclear', 'calestop', 'calempty'], APPROVE = ['capdots', 'captouch'];
  var STEPS_XY = '<b>1</b> Tape the dot sheet flat under the camera. <b>2</b> <b>Move to scan position</b> (top of the ' +
    'page). <b>3</b> <b>Record dot-sheet scan</b>. <b>4</b> For each dot: choose it in the list; <b>Jog</b> over it at ' +
    'travel height; lower with <b>Z-</b>; centre the tip with the X/Y nudge; check from the front and the side; ' +
    '<b>Save nozzle position</b>. <b>5</b> <b>Fit X/Y</b>.';
  var STEPS_Z = '<b>1</b> Take the sheet away; put the flat block under the camera. <b>2</b> <b>Move to scan ' +
    'position</b>. <b>3</b> <b>Record block scan</b>. <b>4</b> At 3 or 4 spots on the block: <b>Jog</b> over the spot at ' +
    'travel height; lower with <b>Z-</b>, 0.1 at the end, until a paper strip is just pinched; tick the box; <b>Save ' +
    'touch</b>; <b>Z+</b> before the next spot. <b>5</b> <b>Fit Z</b>.';
  var DAILY_SUB = 'Once a day before the first job, and after any knock to the head. Bed empty and set up as at the ' +
    'reference. Tick the box at the top, Move to scan position, then Run daily check (about 30 s).';

  function stepClick(e) {
    var b = e.target.closest && e.target.closest('.pistep [data-d]');
    if (!b) return;
    var box = b.parentNode, inp = $id(box.getAttribute('data-id'));
    var st = +box.getAttribute('data-step'), v = (+inp.value || 0) + st * (+b.getAttribute('data-d'));
    v = Math.min(+box.getAttribute('data-max'), Math.max(+box.getAttribute('data-min'), v));
    inp.value = st < 1 ? v.toFixed(2) : Math.round(v);
  }
  function inPage() { return !!page && typeof VIEW !== 'undefined' && VIEW === 'calib'; }
  function say(kind, text) {
    var id = dlg && dlg.open ? 'piadminmsg' : inPage() ? 'pipagemsg' : null;
    if (id && $id(id) && typeof msg === 'function') msg(id, kind, text);
  }
  function admin() { return document.body.classList.contains('pi-admin'); }
  function pane(t) { return page.querySelector('[data-pane="' + t + '"]'); }
  function hide(el) { if (el) el.classList.add('pi-hide'); }
  function boxOf(id) { var e = $id(id); return e && e.closest('label'); }

  /* ---------- PIN box ---------- */
  function openDlg() {
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.id = 'piadmin';
      dlg.innerHTML = '<div class="dlg"><div class="row" style="justify-content:space-between;align-items:center">' +
        '<h2>Admin</h2><button class="btn small" type="button" id="piadminclose">Close</button></div>' +
        '<div id="piadminbody"></div><div class="msg" id="piadminmsg"></div></div>';
      document.body.appendChild(dlg);
      $id('piadminclose').onclick = function () { dlg.close(); };
    }
    if (!dlg.open) dlg.showModal();
    say('', '');
  }
  function openAdmin() {
    if (typeof machineBusy === 'function' && machineBusy()) return;
    post('/api/admin/status', { token: ADMIN_TOKEN }).then(function (st) {
      if (!st.unlocked) ADMIN_TOKEN = null;
      if (st.unlocked) { if (!(admin() && inPage())) showAdminPage(); return; }
      openDlg();
      if (!st.pin_set) showSetPin(true); else showUnlock(st);
    }).catch(function (e) { openDlg(); say('err', e.message); });
  }
  function pinInput(id, label) {
    return '<label class="f" for="' + id + '">' + label + '</label>' +
      '<input id="' + id + '" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off">';
  }
  function pinForm(box, a, b, first, done) {
    box.innerHTML = '<p class="sub">' + (first ? 'Set a 4-digit admin PIN for this machine. Keep it safe: a forgotten ' +
      'PIN can only be reset on this PC (admin_pin_reset.py).' : 'Choose a new 4-digit PIN.') + '</p>' +
      pinInput(a, 'New PIN') + pinInput(b, 'Repeat the PIN') +
      '<button class="btn primary block" type="button" id="' + a + 'save" style="margin-top:10px">Save PIN</button>';
    $id(a + 'save').onclick = function () {
      var p1 = $id(a).value, p2 = $id(b).value;
      if (!/^\d{4}$/.test(p1)) { say('err', 'The PIN must be 4 digits.'); return; }
      if (p1 !== p2) { say('err', 'The two PINs are different.'); return; }
      post('/api/admin/set_pin', { pin: p1, token: ADMIN_TOKEN }).then(function (j) {
        ADMIN_TOKEN = j.token; done();
      }).catch(function (e) { say('err', e.message); });
    };
  }
  function showSetPin() {
    pinForm($id('piadminbody'), 'pipin1', 'pipin2', true, function () { dlg.close(); showAdminPage(); say('good', 'PIN saved.'); });
    $id('pipin1').focus();
  }
  function showUnlock(st) {
    $id('piadminbody').innerHTML = '<p class="sub">Enter the admin PIN.</p>' + pinInput('pipin', 'PIN') +
      '<button class="btn primary block" type="button" id="piunlock" style="margin-top:10px">Unlock</button>';
    if (st && st.blocked_s) say('err', 'Too many wrong PINs - wait ' + st.blocked_s + ' s.');
    $id('piunlock').onclick = function () {
      post('/api/admin/unlock', { pin: $id('pipin').value }).then(function (j) {
        ADMIN_TOKEN = j.token; dlg.close();
        if (admin() && inPage()) say('good', 'Unlocked again - repeat the last step.'); else showAdminPage();
      }).catch(function (e) { say('err', e.message); $id('pipin').value = ''; });
    };
    $id('pipin').focus();
  }
  function lock() {
    post('/api/admin/lock', { token: ADMIN_TOKEN }).catch(function () {});
    ADMIN_TOKEN = null;
    if (page) ['paste', 'pin'].forEach(function (t) { pane(t).innerHTML = ''; });
    document.body.classList.remove('pi-admin');
    go('welcome');
  }
  function checkSession() {
    post('/api/admin/status', { token: ADMIN_TOKEN }).then(function (st) {
      if (st.unlocked || !admin()) return;
      ADMIN_TOKEN = null;
      openAdmin();                                         /* the PIN box over the page; the page stays */
    }).catch(function () {});
  }

  /* ---------- the page ---------- */
  function buildPage() {
    var v = $id('v-calib');
    if (!v || page) return;
    page = document.createElement('div');
    page.id = 'piadminpage';
    page.innerHTML =
      '<div class="pihead"><h1 id="pititle">Admin</h1><button class="btn small" type="button" id="pilockbtn">Lock</button></div>' +
      '<div class="pitabs" id="pitabs">' + TABS.map(function (t) {
        return '<button type="button" class="pitab" data-tab="' + t[0] + '">' + t[1] + '</button>';
      }).join('') + '</div>' +
      '<div class="msg" id="pipagemsg"></div>' +
      '<div class="card" id="pipos"><div class="pipostop"><b>Position</b><span class="chip c-warn" id="piposstate">Checking</span></div>' +
      '<div class="mono" id="piposxyz"></div>' +
      '<p class="pineed" id="piposneed"></p>' +
      '<label class="check pisafe"><input type="checkbox" id="pisafe"><span id="pisafetext"></span></label>' +
      '<div class="pibtns"><button class="btn" type="button" id="piposhome">Home</button>' +
      '<button class="btn primary" type="button" id="piposmove">Move to scan position</button>' +
      '<button class="btn" type="button" id="piposjog">Jog</button></div>' +
      '<div class="msg" id="piposmsg"></div></div>' +
      TABS.map(function (t) { return '<div class="pipane" data-pane="' + t[0] + '" hidden></div>'; }).join('');
    v.insertBefore(page, v.firstChild);

    function put(el, t) { if (el) pane(t).appendChild(el); }
    function card(id) { var e = $id(id); return e && e.closest('.card'); }
    put(card('mlist'), 'machine'); put($id('setupcard'), 'machine'); put(card('calstatus'), 'machine');
    var note = document.createElement('div');
    note.className = 'card'; note.id = 'pinocal';
    note.innerHTML = '<h2>Calibrate</h2><p class="sub">This machine is calibrated and in use. To calibrate it again - after ' +
      'moving the nozzle, the camera or their bracket, or after a failed daily check - open Machine, then ' +
      '<b>Recalibrate the active machine</b>.</p><button class="btn block" type="button" id="pitomachine">Open Machine</button>';
    pane('calibrate').appendChild(note);
    put($id('calibcard'), 'calibrate');
    put(card('calmove'), 'daily'); put($id('calrefcard'), 'daily'); put(card('calcheck'), 'daily'); put(card('calhist'), 'daily');
    var al = document.createElement('div');                 /* camera alignment, live (2026-10-28: "add this tool in */
    al.className = 'card'; al.id = 'pialign';               /* admin section") - read-only, it moves nothing          */
    al.innerHTML = '<h2>Camera alignment (live)</h2><p class="sub">If the camera mount was loose or knocked: head at the ' +
      'scan position, bed as at the reference. Loosen the mount a little, turn the camera while you watch the number, ' +
      'tighten it when it says OK, then Run daily check. Picture down = machine +X, picture right = machine +Y.</p>' +
      '<button class="btn" type="button" id="pialigngo">Start live readout</button>' +
      '<div id="pialignout" hidden style="margin-top:12px"><div class="row" style="gap:10px;align-items:center">' +
      '<span class="chip" id="pialignchip"></span><b class="mono" id="pialignmm" style="font-size:22px"></b>' +
      '<span class="mono hint" id="pialignpx" style="margin:0"></span></div>' +
      '<p id="pialigntip" style="margin:8px 0 0"></p><p class="hint" id="pialignref" style="margin:4px 0 0"></p></div>' +
      '<div class="msg" id="pialignmsg"></div>';
    pane('daily').appendChild(al);
    var sp = document.createElement('div');                 /* the scan position, editable (2026-10-31: "also give    */
    sp.className = 'card'; sp.id = 'piscan';                /* option to edit move to scan in admin panel")           */
    sp.innerHTML = '<h2>Scan position</h2><p class="sub">Where "Move to scan position" takes the head. Jog the head to the ' +
      'spot you want, then <b>Use head position</b> (machine connected and homed), <b>Disconnect</b>, and <b>Save</b>. Or type ' +
      'the numbers. Z must be between the floor and travel Z.</p>' +
      '<div class="mono" id="piscannow" style="margin:6px 0"></div>' +
      '<div class="row" style="gap:6px;flex-wrap:wrap">' +
      '<label class="hint" style="margin:0">X <input id="piscanx" type="number" step="0.001" style="width:96px"></label>' +
      '<label class="hint" style="margin:0">Y <input id="piscany" type="number" step="0.001" style="width:96px"></label>' +
      '<label class="hint" style="margin:0">Z <input id="piscanz" type="number" step="0.001" style="width:96px"></label></div>' +
      '<div class="pibtns" style="margin-top:8px"><button class="btn" type="button" id="piscanhead">Use head position</button>' +
      '<button class="btn primary" type="button" id="piscansave">Save</button></div>' +
      '<p class="hint" id="piscanlim"></p><div class="msg" id="piscanmsg"></div>';
    pane('machine').appendChild(sp);
    $id('piscanhead').onclick = function () { scanLoad(true); };
    $id('piscansave').onclick = scanSave;
    scanLoad(false);
    $id('pialigngo').onclick = function () { if (ALIGN.timer) alignStop(); else alignStart(); };
    hide(v.querySelector('.cols'));
    hide(v.querySelector('.head'));

    /* Studio's separate safety and approval boxes: set from the one tick / the one approval box */
    ALL_SAFE.concat(APPROVE).forEach(function (id) { hide(boxOf(id)); });
    hide($id('calmove')); hide($id('calposhelp')); hide($id('calposstate') && $id('calposstate').parentNode);
    var daily = card('calclear'), sub = daily && daily.querySelector('.sub');
    if (sub) sub.textContent = DAILY_SUB;
    var cc = $id('calibcard');
    hide(cc.querySelector('.sub'));
    cc.querySelectorAll('h2').forEach(function (h) {
      var p = h.nextElementSibling;
      if (!p || !p.classList.contains('hint')) return;
      if (/^A\b/.test(h.textContent)) p.innerHTML = STEPS_XY;
      else if (/^B\b/.test(h.textContent)) p.innerHTML = STEPS_Z;
    });
    [['cxyscan', 'pineedxy'], ['czscan', 'pineedz']].forEach(function (x) {
      var n = document.createElement('p');
      n.className = 'hint pineedline'; n.id = x[1];
      $id(x[0]).parentNode.insertBefore(n, $id(x[0]).nextSibling);
    });
    var cm = document.createElement('div');                /* calibration messages next to the jog buttons too -  */
    cm.className = 'msg'; cm.id = 'picalmsg';              /* Studio's own box is at the bottom of the card        */
    $id('czhelp').parentNode.insertBefore(cm, $id('czhelp'));
    var jogrow = cc.querySelector('[data-cxy]');
    if (jogrow) {
      var jb = document.createElement('button');
      jb.className = 'btn small'; jb.type = 'button'; jb.id = 'picaljog'; jb.textContent = 'Jog 10 mm (travel height)';
      jogrow.parentNode.appendChild(jb);
      jb.onclick = function () { var j = $id('mjog'); if (j) j.click(); };
    }
    var ap = document.createElement('label');
    ap.className = 'check';
    ap.innerHTML = '<input type="checkbox" id="piapok"><span>I checked: every dot in the picture is the right dot, every ' +
      'touch had the paper strip just pinched.</span>';
    $id('capgo').parentNode.insertBefore(ap, $id('capgo'));
    $id('piapok').onchange = function () {
      APPROVE.forEach(function (id) { $id(id).checked = $id('piapok').checked; });
      refresh();
    };

    page.addEventListener('click', stepClick);
    page.querySelectorAll('.pitab').forEach(function (b) { b.onclick = function () { selectTab(b.getAttribute('data-tab')); }; });
    $id('pisafe').onchange = applySafe;
    $id('piposhome').onclick = function () { var h = $id('mhome'); if (h && !$id('piposhome').disabled) h.click(); };
    $id('piposmove').onclick = moveToScan;
    $id('piposjog').onclick = function () { var j = $id('mjog'); if (j && !$id('piposjog').disabled) j.click(); };
    $id('pilockbtn').onclick = lock;
    $id('pitomachine').onclick = function () { selectTab('machine'); };
  }
  function showAdminPage(tab) {
    if (typeof machineBusy === 'function' && machineBusy()) { alert('Wait until the machine has finished.'); return; }
    buildPage();
    document.body.classList.remove('pi-daily');
    document.body.classList.add('pi-admin');
    $id('pititle').textContent = 'Admin';
    if (!inPage()) go('calib');
    selectTab(tab || (typeof calibActive === 'function' && calibActive() ? 'calibrate' : 'machine'));
  }
  function showDaily() {
    if (typeof machineBusy === 'function' && machineBusy()) return;
    buildPage();
    document.body.classList.remove('pi-admin');
    document.body.classList.add('pi-daily');              /* only the daily check - no tabs, no reference, no PIN */
    $id('pititle').textContent = 'Daily check';
    if (!inPage()) go('calib');
    selectTab('daily');
  }
  function scanShow(v) { return 'X' + v[0].toFixed(3) + ' Y' + v[1].toFixed(3) + ' Z' + v[2].toFixed(3); }
  function scanLoad(fromHead) {
    getj('/api/admin/scan_position').then(function (r) {
      if (r.error) { msg('piscanmsg', 'err', r.error); return; }
      $id('piscannow').textContent = r.machine + ': now ' + scanShow(r.scan_pose);
      $id('piscanlim').textContent = 'Work area X 0-' + r.work_area[0] + ', Y 0-' + r.work_area[1] + '. Z floor ' + r.z_floor +
        ', travel Z ' + r.travel_z + '.';
      if (fromHead) {
        if (!r.head) { msg('piscanmsg', 'warn', 'The head position is only known when the machine is connected, homed and Idle.'); return; }
        ['x', 'y', 'z'].forEach(function (k, i) { $id('piscan' + k).value = r.head[i].toFixed(3); });
        msg('piscanmsg', 'good', 'Head position copied. Disconnect, then Save.');
      } else if (!$id('piscanx').value) {
        ['x', 'y', 'z'].forEach(function (k, i) { $id('piscan' + k).value = r.scan_pose[i].toFixed(3); });
      }
    }).catch(function (e) { msg('piscanmsg', 'err', e.message); });
  }
  function scanSave() {
    var b = { confirm: true, x: $id('piscanx').value, y: $id('piscany').value, z: $id('piscanz').value };
    msg('piscanmsg', 'info', 'Saving');
    post('/api/admin/scan_position', b).then(function (r) {
      scanLoad(false);
      msg('piscanmsg', 'good', 'Saved: ' + scanShow(r.scan_pose) + '. Now set a new daily-check reference (Daily check, ' +
        'bed empty, no hand).');
    }).catch(function (e) { msg('piscanmsg', 'err', e.message); });
  }
  var ALIGN = { timer: null, hist: [] };
  var ALIGN_LABEL = { ok: ['c-ok', 'OK - tighten it here'], close: ['c-warn', 'Close'], move: ['c-bad', 'Move it'] };
  function alignStart() {
    ALIGN.hist = [];
    $id('pialigngo').textContent = 'Stop';
    $id('pialignout').hidden = false;
    msg('pialignmsg', '', '');
    alignTick();
    ALIGN.timer = setInterval(alignTick, 1000);
  }
  function alignStop() {
    if (ALIGN.timer) clearInterval(ALIGN.timer);
    ALIGN.timer = null;
    if ($id('pialigngo')) $id('pialigngo').textContent = 'Start live readout';
  }
  function alignTick() {
    api('/api/admin/camera_align').then(function (r) {
      if (!ALIGN.timer) return;
      if (!r.ready) { msg('pialignmsg', 'info', r.reason); return; }
      msg('pialignmsg', r.at_scan_position ? '' : 'warn', r.at_scan_position ? '' :
        'The head is not at the scan position - the number only means something there (Move to scan position above).');
      ALIGN.hist = ALIGN.hist.concat([[r.x_px, r.y_px, r.shift_mm, r.state, r.tips]]).slice(-3);
      var n = ALIGN.hist.length, x = 0, y = 0, mm = 0;
      ALIGN.hist.forEach(function (h) { x += h[0] / n; y += h[1] / n; mm += h[2] / n; });
      var last = ALIGN.hist[n - 1], st = mm <= 0.3 ? 'ok' : mm <= 0.7 ? 'close' : 'move', lab = ALIGN_LABEL[st];
      $id('pialignchip').className = 'chip ' + lab[0]; $id('pialignchip').textContent = lab[1];
      $id('pialignmm').textContent = mm.toFixed(2) + ' mm';
      $id('pialignpx').textContent = '(picture x ' + (x >= 0 ? '+' : '') + x.toFixed(1) + ' px, y ' + (y >= 0 ? '+' : '') +
        y.toFixed(1) + ' px)';
      $id('pialigntip').textContent = st === 'ok' ? 'Good - tighten the mount here and check it stays OK.' :
        (last[4].length ? last[4].join('; ') + '.' : '');
      $id('pialignref').textContent = 'Against the reference of ' + String(r.reference || '-').replace('T', ' ') +
        '. The daily check has the final word.';
    }).catch(function (e) { if (ALIGN.timer) msg('pialignmsg', 'err', e.message); });
  }
  function selectTab(t) {
    alignStop();
    TAB = t;
    page.querySelectorAll('.pitab').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === t); });
    page.querySelectorAll('.pipane').forEach(function (p) { p.hidden = p.getAttribute('data-pane') !== t; });
    $id('pipos').hidden = !SAFE[t];
    $id('pisafetext').textContent = SAFE[t] ? SAFE[t][0] : '';
    say('', '');
    if (typeof msg === 'function') msg('piposmsg', '', '');
    clearSafe();
    if (t === 'paste') renderPaste(); else if (t === 'pin') renderPin();
    if (admin()) checkSession();
    window.scrollTo({ top: 0 });
  }
  function clearSafe() {
    if (!page) return;
    $id('pisafe').checked = false;
    ALL_SAFE.forEach(function (id) { var e = $id(id); if (e) e.checked = false; });
    refresh();
  }
  function applySafe() {
    var on = $id('pisafe').checked, ids = SAFE[TAB] ? SAFE[TAB][1] : [];
    ALL_SAFE.forEach(function (id) { var e = $id(id); if (e) e.checked = on && ids.indexOf(id) >= 0; });
    refresh();
  }
  function refresh() {
    try { if (typeof calibShow === 'function') calibShow(); } catch (e) { /* calibration view not ready */ }
    showPos();
  }
  function moveToScan() {
    if ($id('piposmove').disabled) return;
    msg('piposmsg', 'info', 'Moving: up to travel height, across, then down to the scan height.');
    machineCommand('/api/calibration/scan_position', { confirm_clear: true, confirm_estop: true }, 'scan_position')
      .then(function () { msg('piposmsg', '', ''); })
      .catch(function (e) { msg('piposmsg', 'err', e.message); })
      .then(refresh);
  }
  function fmt2(v) { return (+v).toFixed(2); }
  function machineNow() {
    var m = (typeof MACH !== 'undefined' && MACH) || {};
    var fresh = typeof machFresh === 'function' && machFresh();
    var busy = (typeof machineBusy === 'function' && machineBusy()) ||
      (typeof calBusy !== 'undefined' && calBusy) || (typeof cBusy !== 'undefined' && cBusy) || '';
    return { m: m, fresh: fresh, busy: busy, can: fresh && m.connected && m.homed === true && m.state === 'Idle' && !busy,
             at: m.capture_ready === true };
  }
  function need(n) {
    var tick = $id('pisafe').checked ? '' : 'Tick the box below first. ';
    var go2 = n.at ? '<span class="pi-ok">The head is at the scan position.</span>' :
      tick + 'Then tap <b>Move to scan position</b>.';
    if (TAB === 'daily') {
      var ref = typeof CALST !== 'undefined' && CALST && CALST.reference;
      if (!ref && admin()) return 'No reference yet: right after a good calibration, empty bed, head at the scan position - ' +
        '<b>Set the reference</b> below. ' + go2;
      return (n.at ? 'Ready: tap <b>Run daily check</b> below. ' : 'Needs the head at the scan position. ') + go2;
    }
    if (TAB === 'calibrate') {
      if (typeof calibActive !== 'function' || !calibActive()) return 'Not in calibration mode - see below.';
      var s = typeof CSESS !== 'undefined' ? CSESS : null;
      if (!s) return '';
      var pts = Object.keys(s.xy_points || {}).length, dots = s.xy_scan ? s.xy_scan.dots.length : 0;
      if (!s.xy_scan) return '<b>Step 1 of 5 - dot sheet.</b> Sheet flat on the bed, head at the scan position, then ' +
        '<b>Record dot-sheet scan</b>. ' + go2;
      if (!s.xy_fit) return '<b>Step 2 of 5 - dots.</b> Save the nozzle over each dot (' + pts + ' of ' + dots +
        ' saved; at least 3), then <b>Fit X/Y</b>.';
      if (s.xy_fit.verdict !== 'GOOD') return '<b>X/Y fit is not good</b> - see its problems below; save the dots again, ' +
        'then Fit X/Y.';
      if (!s.z_scan) return '<b>Step 3 of 5 - block.</b> Block under the camera, head at the scan position, then ' +
        '<b>Record block scan</b>. ' + go2;
      if (!s.z_fit) return '<b>Step 4 of 5 - touches.</b> Touch the block at 3 or 4 spots (' + (s.z_touches || []).length +
        ' saved; at least 2), then <b>Fit Z</b>.';
      if (s.z_fit.verdict !== 'GOOD') return '<b>Z fit is not good</b> - see its problems below; touch again, then Fit Z.';
      return '<b>Step 5 of 5 - approve.</b> Check both fits, then Approve at the bottom.';
    }
    return 'Home and Move to scan position work from here. Choose the machine only while it is disconnected.';
  }
  function showPos() {
    if (!page || $id('pipos').hidden) return;
    var n = machineNow(), m = n.m, st, k;
    if (n.busy) { st = n.busy === 'recording' ? 'Recording - keep still' : 'Moving'; k = 'info'; }
    else if (!n.fresh) { st = 'Waiting for the machine'; k = 'warn'; }
    else if (!m.connected) { st = 'Not connected'; k = 'bad'; }
    else if (!m.homed) { st = 'Home first'; k = 'warn'; }
    else if (m.state !== 'Idle') { st = m.state; k = 'warn'; }
    else if (n.at) { st = 'At the scan position'; k = 'ok'; }
    else { st = 'Not at the scan position'; k = 'warn'; }
    var c = $id('piposstate');
    c.className = 'chip c-' + k; c.textContent = st;
    $id('piposxyz').textContent = m.mpos ? 'X ' + fmt2(m.mpos[0]) + '   Y ' + fmt2(m.mpos[1]) + '   Z ' + fmt2(m.mpos[2]) : '';
    $id('piposneed').innerHTML = need(n);
    $id('piposmove').disabled = !($id('pisafe').checked && n.can) || n.at;
    $id('piposhome').disabled = !(n.fresh && m.connected) || !!n.busy;
    $id('piposjog').disabled = !(n.fresh && m.connected) || !!n.busy;
    $id('pinocal').hidden = !$id('calibcard').hidden;
    var line = n.at ? 'The head is at the scan position.' : 'Needs the head at the scan position - Move to scan position at the top.';
    ['pineedxy', 'pineedz'].forEach(function (id) { var e = $id(id); if (e) { e.textContent = line; e.classList.toggle('pi-okline', n.at); } });
    var h = $id('czhelp');
    if (h && /^Only straight up/.test(h.textContent)) h.textContent = 'Z- 10 stops 10 mm above the floor; finish with 1 and ' +
      '0.1. With the block, Z- 10 only while the tip is more than 15 mm above it. X/Y nudge: 1 / 0.1 mm at any height; ' +
      'bigger moves: Jog, at travel height.';
    else if (h && /^Tick the box above/.test(h.textContent)) h.textContent = 'Tick the box at the top of the page first.';
  }

  /* ---------- Paste and PIN tabs ---------- */
  function stepper(id, value, step, min, max) {
    return '<div class="pistep" data-id="' + id + '" data-step="' + step + '" data-min="' + min + '" data-max="' + max + '">' +
      '<button type="button" class="btn" data-d="-1" aria-label="Less">-</button>' +
      '<input id="' + id + '" type="number" inputmode="decimal" value="' + value + '" step="' + step + '" min="' + min + '" max="' + max + '">' +
      '<button type="button" class="btn" data-d="1" aria-label="More">+</button></div>';
  }
  function renderPaste() {
    var s = SETUP || { dispenser: 'none' };
    var d = (DISP && DISP[s.dispenser]) || { start_s: 0.3, stop_s: 0.2 };
    var start = s.start_s != null ? s.start_s : d.start_s, stop = s.stop_s != null ? s.stop_s : d.stop_s;
    function radio(v, label) {
      return '<label class="check"><input type="radio" name="pidisp" value="' + v + '"' + (s.dispenser === v ? ' checked' : '') +
        '><span>' + label + '</span></label>';
    }
    pane('paste').innerHTML = '<h2>Paste</h2><p class="sub">The dispenser every program uses.</p>' + radio('none', 'None - hover only') +
      radio('pneumatic', 'Pneumatic air valve (DLC32 TTL)') + radio('servo_ssr', 'Servo dispenser (SSR on the DLC32 TTL)') +
      '<label class="f">Start delay (s)</label>' + stepper('pistart', start, 0.05, 0, 3) +
      '<label class="f">Stop delay (s)</label>' + stepper('pistop', stop, 0.05, 0, 3) +
      '<div class="row" style="gap:8px;margin-top:8px"><button class="btn primary" type="button" id="pisetupsave" style="flex:1">Save paste</button>' +
      '<button class="btn" type="button" id="pitest" style="flex:1">Test 1 s</button></div>';
    function chosen() { var r = document.querySelector('input[name="pidisp"]:checked'); return r ? r.value : 'none'; }
    $id('pisetupsave').onclick = function () {
      post('/api/admin/paste_setup', { dispenser: chosen(), start_s: +$id('pistart').value, stop_s: +$id('pistop').value })
        .then(function (j) { SETUP = j.setup; applySetup(); renderChips(); say('good', 'Paste saved: ' + (SETUP.label || 'none') + '.'); })
        .catch(function (e) { say('err', e.message); });
    };
    $id('pitest').onclick = function () {
      var dsp = chosen();
      if (dsp === 'none') { say('err', 'Choose a dispenser first.'); return; }
      say('info', 'Dispenser on for 1 s');
      api('/api/machine/dispense_test', { seconds: 1, dispenser: dsp })
        .then(function () { setTimeout(function () { say('good', 'Done - did it switch for about 1 s?'); }, 1500); })
        .catch(function (e) { say('err', e.message); });
    };
    document.querySelectorAll('input[name="pidisp"]').forEach(function (r) {
      r.onchange = function () {
        var x = DISP && DISP[chosen()];
        if (x) { $id('pistart').value = x.start_s; $id('pistop').value = x.stop_s; }
      };
    });
  }
  function renderPin() {
    var p = pane('pin');
    p.innerHTML = '<h2>PIN</h2><div id="pipinform"></div>' +
      '<button class="btn block" type="button" id="pilock2" style="margin-top:14px">Lock Admin now</button>';
    pinForm($id('pipinform'), 'pinew1', 'pinew2', false, function () { renderPin(); say('good', 'New PIN saved.'); });
    $id('pilock2').onclick = lock;
  }
  /* Jog in Admin: X/Y shown even when the last job opened was a real-hand job (the Pi server allows them with the
     Admin session - see pi_jog); Z free between Z 0 and the machine's Z floor, X/Y at any height down to it inside the
     work area (user 2026-10-18: "i want z to move freely under admin panel", "now x and y should aslo move no
     restriction in admin panel"; the server checks it again - henna_preview/admin_jog.py). Runs after the page's own
     Jog button handler, which hides X/Y. */
  var ZLIM = null;
  document.addEventListener('click', function (e) {
    if (!(e.target.closest && e.target.closest('#mjog')) || !admin()) return;
    document.querySelectorAll('#dlgjog [data-jog^="X"],#dlgjog [data-jog^="Y"]').forEach(function (b) { b.hidden = false; });
    if (typeof msg === 'function') msg('msgjog', '', '');
    getj('/api/admin/jog_limits').then(function (j) { ZLIM = j; if (typeof jogShow === 'function') jogShow(); })
      .catch(function () { ZLIM = null; });
    if (typeof jogShow === 'function') jogShow();
  });
  function adminZ() {
    var d = $id('dlgjog');
    if (!d || !d.open || !admin() || !ZLIM || ZLIM.z_floor == null) return;
    var m = (typeof MACH !== 'undefined' && MACH) || {}, busy = typeof machineBusy === 'function' && machineBusy();
    var z = m.mpos ? m.mpos[2] : null, step = typeof jogStep !== 'undefined' ? jogStep : 1;
    var ok = typeof machFresh === 'function' && machFresh() && m.connected && m.homed === true && m.state === 'Idle' &&
      !busy && $id('jnoperson').checked && z != null;
    var dn = document.querySelector('#dlgjog [data-jog="Z,-1"]'), up = document.querySelector('#dlgjog [data-jog="Z,1"]');
    if (dn) dn.disabled = !(ok && +(z - step).toFixed(3) >= ZLIM.z_floor - 1e-9);
    if (up) up.disabled = !(ok && +(z + step).toFixed(3) <= ZLIM.z_top + 1e-9);
    var lim = typeof JOG_XY !== 'undefined' ? JOG_XY : [225, 176];
    document.querySelectorAll('#dlgjog [data-jog^="X"],#dlgjog [data-jog^="Y"]').forEach(function (b) {
      var p = b.getAttribute('data-jog').split(','), i = p[0] === 'X' ? 0 : 1, t = ok ? +(m.mpos[i] + (+p[1]) * step).toFixed(3) : 0;
      b.disabled = !(ok && z >= ZLIM.z_floor - 1e-9 && t >= 0 && t <= lim[i] + 1e-9);
    });
    var tz = typeof TRAVEL_Z !== 'undefined' ? TRAVEL_Z : -5;
    $id('jogsub').textContent = 'Admin: every axis moves freely - Z between Z ' + ZLIM.z_top + ' and the floor Z ' +
      ZLIM.z_floor + ', X and Y at any height inside the work area (slower below travel height Z ' + tz + ').';
    if (ok && z < tz - 0.05) $id('joghelp').textContent = 'Below travel height: X and Y move slowly (F300). ' +
      'Keep the nozzle clear of the block and the sheet.';
  }
  if (typeof jogShow === 'function') {
    var origJogShow = jogShow;
    jogShow = function () { var r = origJogShow.apply(this, arguments); try { adminZ(); } catch (e) { /* no dialog */ } return r; };
  }

  /* Studio's own functions, wrapped: entering the view clears the one tick (as Studio clears its boxes), each
     calibration redraw updates the Position card, and leaving the view leaves Admin / daily mode. The calibration
     view reached any other way is the operator's daily check (Admin needs the PIN). */
  if (typeof showCalib === 'function') {
    var origShowCalib = showCalib;
    showCalib = function () {
      var r = origShowCalib.apply(this, arguments);
      if (page) clearSafe();
      return r;
    };
  }
  /* Studio's messages: a calibration message is also shown next to the jog buttons; "Admin is locked" (the session
     ended) opens the PIN box at once instead of only saying so. */
  if (typeof msg === 'function') {
    var origMsg = msg;
    msg = function (id, kind, t) {
      var r = origMsg.apply(this, arguments);
      if (id === 'msgcalib' && $id('picalmsg')) origMsg('picalmsg', kind, t);
      if (kind === 'err' && /Admin is locked/.test(String(t || '')) && admin()) {
        ADMIN_TOKEN = null;
        openAdmin();
      }
      return r;
    };
  }
  if (typeof calibCardShow === 'function') {
    var origCardShow = calibCardShow;
    calibCardShow = function () { var r = origCardShow.apply(this, arguments); try { showPos(); } catch (e) { /* not built */ } return r; };
  }
  if (typeof go === 'function') {
    var origGoAdmin = go;
    go = function (view) {
      if (view !== 'calib') { document.body.classList.remove('pi-admin', 'pi-daily'); alignStop(); }
      else if (!admin() && !document.body.classList.contains('pi-daily')) {
        document.body.classList.add('pi-daily');
        var r = origGoAdmin.apply(this, arguments);
        buildPage(); $id('pititle').textContent = 'Daily check'; selectTab('daily');
        return r;
      }
      return origGoAdmin.apply(this, arguments);
    };
  }

  buildHome();
  loadSetup();
  setInterval(tick, 500);
})();
