/* Henna Studio - Pi drag-to-scroll (user 2026-10-14: "not able to slide with my fingers").
   On the Pi the browser may get the touchscreen as a MOUSE, and a mouse drag does not scroll a page. Here a press
   that moves more than 8 px vertically scrolls the nearest scrollable box (a dialog or the page), with a short
   glide after release; the click that would end that drag is swallowed, so a drag that starts on a button scrolls
   instead of pressing it. A short tap is untouched. Real touch events (if the browser sends them) scroll natively
   and are left alone. Never on the drawing pictures, sliders, inputs or the X/Y nudge / jog buttons' own drags.
   Added by webui_server_pi.py; the laptop page never gets it. */
(function () {
  'use strict';
  var THRESHOLD = 8, FRICTION = 0.92, MIN_V = 0.02;
  var drag = null, swallowClick = false, glide = 0;

  function noScroll(t) {
    return t.closest('canvas, input, textarea, select, [contenteditable], [data-noscroll]');
  }
  function scrollBox(t) {
    for (var el = t; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      var oy = getComputedStyle(el).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) return el;
    }
    return document.scrollingElement || document.documentElement;
  }

  addEventListener('pointerdown', function (e) {
    cancelAnimationFrame(glide);
    if (e.pointerType === 'touch' || e.button !== 0 || noScroll(e.target)) { drag = null; return; }
    var box = scrollBox(e.target);
    drag = { box: box, y0: e.clientY, top0: box.scrollTop, moved: false, lastY: e.clientY, lastT: e.timeStamp, v: 0 };
  }, true);

  addEventListener('pointermove', function (e) {
    if (!drag) return;
    var dy = e.clientY - drag.y0;
    if (!drag.moved && Math.abs(dy) < THRESHOLD) return;
    drag.moved = true;
    drag.box.scrollTop = drag.top0 - dy;
    var dt = Math.max(1, e.timeStamp - drag.lastT);
    drag.v = 0.8 * ((e.clientY - drag.lastY) / dt) + 0.2 * drag.v;      // px per ms, smoothed
    drag.lastY = e.clientY; drag.lastT = e.timeStamp;
    e.preventDefault();
  }, { capture: true, passive: false });

  function end() {
    if (!drag) return;
    var d = drag; drag = null;
    if (!d.moved) return;
    swallowClick = true;
    setTimeout(function () { swallowClick = false; }, 400);
    var v = -d.v, t0 = performance.now();
    (function step(now) {
      var dt = now - t0; t0 = now;
      if (Math.abs(v) < MIN_V) return;
      d.box.scrollTop += v * dt;
      v *= Math.pow(FRICTION, dt / 16);
      glide = requestAnimationFrame(step);
    })(t0);
  }
  addEventListener('pointerup', end, true);
  addEventListener('pointercancel', function () { drag = null; }, true);

  addEventListener('click', function (e) {
    if (swallowClick) { swallowClick = false; e.preventDefault(); e.stopPropagation(); }
  }, true);
  addEventListener('dragstart', function (e) { if (!noScroll(e.target)) e.preventDefault(); }, true);   // no image drag
})();

/* Confirm / alert boxes that fit the screen (user 2026-10-14: the browser's own "127.0.0.1:8765 says" box is wider
   than the portrait screen at x2, its OK button is off the edge). The page uses confirm() synchronously
   (`if(!confirm(..))return`), so: the first call shows Studio's own box and returns false (nothing happens yet);
   OK replays the same click / change on the same element, and that second confirm() of the same text returns true.
   Cancel = nothing, as with the browser's box. alert() shows the same box with one OK. */
(function () {
  'use strict';
  var last = null, approved = null, approvedAt = 0;
  addEventListener('click', function (e) { last = { el: e.target, type: 'click' }; }, true);
  addEventListener('change', function (e) { last = { el: e.target, type: 'change' }; }, true);

  function box(msg, buttons) {
    var d = document.getElementById('pi-dlg');
    if (!d) {
      d = document.createElement('dialog');
      d.id = 'pi-dlg';
      d.innerHTML = '<div class="dlg" style="padding:16px"><p class="pi-msg" style="font-size:16px;line-height:1.45;' +
        'margin:0 0 16px;white-space:pre-wrap;overflow-wrap:anywhere"></p><div class="pi-foot" style="display:flex;gap:10px"></div></div>';
      document.body.appendChild(d);
    }
    d.querySelector('.pi-msg').textContent = String(msg);
    var foot = d.querySelector('.pi-foot');
    foot.innerHTML = '';
    buttons.forEach(function (b) {
      var x = document.createElement('button');
      x.type = 'button';
      x.className = 'btn' + (b.primary ? ' primary' : '');
      x.style.flex = '1 1 0';
      x.textContent = b.label;
      x.onclick = function () { d.close(); if (b.run) b.run(); };
      foot.appendChild(x);
    });
    if (!d.open) d.showModal();
  }

  window.alert = function (msg) { box(msg, [{ label: 'OK', primary: true }]); };
  window.confirm = function (msg) {
    msg = String(msg);
    if (approved === msg && Date.now() - approvedAt < 3000) { approved = null; return true; }
    var src = last;
    box(msg, [{ label: 'Cancel' }, { label: 'OK', primary: true, run: function () {
      if (!src || !src.el || !src.el.isConnected) return;
      approved = msg; approvedAt = Date.now();
      if (src.type === 'change') src.el.dispatchEvent(new Event('change', { bubbles: true }));
      else src.el.click();
    } }]);
    return false;
  };
})();

/* Home in one tap (user 2026-10-14: "remove this" - the Home dialog with its two ticks). Homing always lifts Z first
   ($HZ) and then moves X/Y at the top, about 63 mm above the bed on the ball-screw machine. The same command and the
   same guards as the dialog's "Home now": machine connected, fresh status, nothing busy; errors in Studio's box. */
(function () {
  'use strict';
  addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('#mhome');
    if (!b || b.disabled) return;
    e.stopPropagation(); e.preventDefault();                 // instead of opening the dialog
    if (!machFresh() || !MACH || !MACH.connected || machineBusy()) return;
    machineCommand('/api/machine/home', { confirm_clear: true, confirm_estop: true }, 'homing')
      .catch(function (err) { alert(err.message); });
  }, true);
})();

/* Move to scan position in one tap (user 2026-10-14: "remove this also" - the dialog with its two ticks). The route
   rises to travel Z first, moves X/Y there (about 58 mm above the bed on the ball-screw machine), then to the scan Z.
   Same command and guards as the dialog's button (scanCanMove: connected, homed, fresh, not busy; a job). */
(function () {
  'use strict';
  addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('#scanmove');
    if (!b || b.disabled) return;
    e.stopPropagation(); e.preventDefault();
    if (!scanCanMove() || !JOB) return;
    machineCommand('/api/machine/scan_position', { job: JOB.id, confirm_clear: true, confirm_estop: true }, 'scan_position')
      .catch(function (err) { alert(err.message); });
  }, true);
})();

/* Hardware PAUSE button state in the machine bar (2026-10-15, henna_preview/pause_button.py): green "Pause btn" when
   GPIO17 is being watched, red "Pause btn OFF" when it is not (then the push button does nothing - use the screen).
   Checked every 3 s; the server not answering also shows OFF. */
(function () {
  'use strict';
  var chip = document.createElement('span');
  chip.id = 'pibtn';
  chip.className = 'chip c-warn';
  chip.textContent = 'Pause btn ?';
  function place() {
    var st = document.getElementById('mstate');
    if (st && !chip.parentNode) st.parentNode.insertBefore(chip, st.nextSibling);
  }
  function show(ok, why) {
    place();
    chip.className = 'chip ' + (ok ? 'c-ok' : 'c-bad');
    chip.textContent = ok ? 'Pause btn' : 'Pause btn OFF';
    chip.title = why;
  }
  function poll() {
    fetch('/api/pi/pause_button').then(function (r) { return r.json(); }).then(function (j) {
      show(!!j.ready, j.ready ? 'GPIO' + j.pin + ' watched' : 'not working: ' + (j.error || 'not started'));
    }).catch(function () { show(false, 'Studio server not answering'); });
  }
  poll();
  setInterval(poll, 3000);
})();


/* Pi home, Admin and the run bar (2026-10-16, user: "redesign it for more pi focused" + "all the calibration,
   machine selection and valve selection option should come under admin panel", 4-digit PIN).
   - Home: chips (machine, paste) and big tiles - New henna job, Saved jobs, Admin.
   - Admin (PIN; the Pi server refuses admin routes without the session token): one page with tabs - Machine,
     Calibrate, Daily check, Paste (dispenser + delays, test, servo angles), Storage, PIN (change, lock) - see below.
     Forgotten PIN: raspberry_pi/admin_pin_reset.py.
   - Program step: shows the Admin's paste setup; the server applies it to every program.
   - Bottom bar: Pause / Resume next to STOP while a program runs.
   The same Studio routes and checks as before - nothing here moves the machine on its own. */
(function () {
  'use strict';
  var ADMIN_TOKEN = null, SETUP = null, DISP = null, STORE = null;
  var ADMIN_RE = /^\/api\/(calibration\/|machines\/(select|add|recalibrate|setup_area|setup_scan)$|pi\/servo\/|pi\/paste_setup$|pi\/storage\/)/;
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
  function loadStorage() {
    return getj('/api/pi/storage').then(function (j) { STORE = j; renderChips(); return j; }).catch(function () {});
  }
  function loadSetup() {
    return getj('/api/pi/paste_setup').then(function (j) { SETUP = j.setup; DISP = j.dispensers; applySetup(); renderChips(); return j; })
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
      b.className = 'btn small'; b.id = 'pihomebtn'; b.type = 'button'; b.textContent = 'Home';
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
    if (STORE) out += chip(STORE.free_gb < STORE.min_free_gb ? 'bad' : STORE.free_gb < 2 * STORE.min_free_gb ? 'warn' : 'ok',
      'Storage: ' + STORE.free_gb + ' GB free');
    if (!SETUP) out += chip('warn', 'Paste: not set (Admin)');
    else out += chip(SETUP.dispenser === 'none' ? 'info' : 'ok', 'Paste: ' + (SETUP.dispenser === 'none' ? 'none' : SETUP.label));
    c.innerHTML = out;
  }

  /* ---------- Pause / Resume in the bottom bar ---------- */
  function buildRunBar() {
    var stop = $id('mstop');
    if (!stop || $id('pipause')) return;
    var b = document.createElement('button');
    b.className = 'btn small'; b.id = 'pipause'; b.type = 'button'; b.hidden = true; b.textContent = 'Pause';
    stop.parentNode.insertBefore(b, stop);
    b.onclick = function () {
      var p = (typeof prog === 'function') ? prog() : null;
      if (!p) return;
      b.disabled = true;
      api(p.paused ? '/api/machine/resume' : '/api/machine/pause', {})
        .then(function (m) { if (typeof machineAccept === 'function') machineAccept(m); })
        .catch(function (e) { alert(e.message); })
        .then(function () { b.disabled = false; });
    };
  }
  function tick() {
    var b = $id('pipause');
    if (b) {
      var run = (typeof running === 'function') && running();
      var p = run && (typeof prog === 'function') ? prog() : null;
      b.hidden = !run;
      b.textContent = p && p.paused ? 'Resume' : 'Pause';
    }
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
     Studio's Calibration view, rearranged: tabs Machine / Calibrate / Daily check / Paste / Storage / PIN, and on top a
     Position card - the machine's state and X Y Z, what the current step needs, ONE safety tick and its own Home,
     Move to scan position (the calibration route, allowed in calibration mode) and Jog - so nothing needs a job's
     Scan step or the Daily check card any more. The one tick stands for that tab's separate safety boxes (Studio's
     own boxes stay, hidden, and are set from it); it clears when the tab changes or the page is left. The three
     approval boxes become one. The same routes and the same server checks as before.
     Daily check tile (operators, no PIN): the same page with only the Position card and the daily check. */
  var dlg = null, page = null, TAB = 'machine';
  var TABS = [['machine', 'Machine'], ['calibrate', 'Calibrate'], ['daily', 'Daily check'],
              ['paste', 'Paste'], ['storage', 'Storage'], ['pin', 'PIN']];
  var SAFE = {                       /* the one tick's words on each tab, and the Studio boxes it stands for */
    machine: ['No hand, prosthetic or person under the machine, and I am at the emergency stop.', ['calclear', 'calestop']],
    calibrate: ['Only the dot sheet or the block is on the bed - no hand, no prosthetic, no person - and I am at the ' +
                'emergency stop.', ['ctargets', 'calclear', 'calestop']],
    daily: ['The bed is empty and set up as at the reference - no hand, no person - and I am at the emergency stop.',
            ['calclear', 'calestop', 'calempty']]
  };
  var ALL_SAFE = ['ctargets', 'calclear', 'calestop', 'calempty'], APPROVE = ['capdots', 'captouch', 'capair'];
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
    post('/api/pi/admin/status', { token: ADMIN_TOKEN }).then(function (st) {
      if (!st.unlocked) ADMIN_TOKEN = null;
      if (st.unlocked) { showAdminPage(); return; }
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
      'PIN can only be reset at the Pi itself.' : 'Choose a new 4-digit PIN.') + '</p>' +
      pinInput(a, 'New PIN') + pinInput(b, 'Repeat the PIN') +
      '<button class="btn primary block" type="button" id="' + a + 'save" style="margin-top:10px">Save PIN</button>';
    $id(a + 'save').onclick = function () {
      var p1 = $id(a).value, p2 = $id(b).value;
      if (!/^\d{4}$/.test(p1)) { say('err', 'The PIN must be 4 digits.'); return; }
      if (p1 !== p2) { say('err', 'The two PINs are different.'); return; }
      post('/api/pi/admin/set_pin', { pin: p1, token: ADMIN_TOKEN }).then(function (j) {
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
      post('/api/pi/admin/unlock', { pin: $id('pipin').value }).then(function (j) {
        ADMIN_TOKEN = j.token; dlg.close(); showAdminPage();
      }).catch(function (e) { say('err', e.message); $id('pipin').value = ''; });
    };
    $id('pipin').focus();
  }
  function lock() {
    post('/api/pi/admin/lock', { token: ADMIN_TOKEN }).catch(function () {});
    ADMIN_TOKEN = null;
    if (page) ['paste', 'storage', 'pin'].forEach(function (t) { pane(t).innerHTML = ''; });
    document.body.classList.remove('pi-admin');
    go('welcome');
  }
  function checkSession() {
    post('/api/pi/admin/status', { token: ADMIN_TOKEN }).then(function (st) {
      if (st.unlocked || !admin()) return;
      ADMIN_TOKEN = null;
      document.body.classList.remove('pi-admin');
      go('welcome');
      openAdmin();
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
      'touch had the paper strip just pinched, and the first program on this machine will be an air run (no hand).</span>';
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
  function selectTab(t) {
    TAB = t;
    page.querySelectorAll('.pitab').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === t); });
    page.querySelectorAll('.pipane').forEach(function (p) { p.hidden = p.getAttribute('data-pane') !== t; });
    $id('pipos').hidden = !SAFE[t];
    $id('pisafetext').textContent = SAFE[t] ? SAFE[t][0] : '';
    say('', '');
    if (typeof msg === 'function') msg('piposmsg', '', '');
    clearSafe();
    if (t === 'paste') renderPaste(); else if (t === 'storage') renderStorage(); else if (t === 'pin') renderPin();
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

  /* ---------- Paste, Storage, PIN tabs ---------- */
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
      radio('pneumatic', 'Pneumatic air valve (DLC32 TTL)') + radio('servo_ssr', 'Servo pinch valve (this Pi)') +
      '<label class="f">Start delay (s)</label>' + stepper('pistart', start, 0.05, 0, 3) +
      '<label class="f">Stop delay (s)</label>' + stepper('pistop', stop, 0.05, 0, 3) +
      '<div class="row" style="gap:8px;margin-top:8px"><button class="btn primary" type="button" id="pisetupsave" style="flex:1">Save paste</button>' +
      '<button class="btn" type="button" id="pitest" style="flex:1">Test 1 s</button></div>' +
      '<div id="piservobox"></div>';
    function chosen() { var r = document.querySelector('input[name="pidisp"]:checked'); return r ? r.value : 'none'; }
    $id('pisetupsave').onclick = function () {
      post('/api/pi/paste_setup', { dispenser: chosen(), start_s: +$id('pistart').value, stop_s: +$id('pistop').value })
        .then(function (j) { SETUP = j.setup; applySetup(); renderChips(); say('good', 'Paste saved: ' + (SETUP.label || 'none') + '.'); renderServo(); })
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
        renderServo();
      };
    });
    renderServo();
  }
  function renderStorage() {
    pane('storage').innerHTML = '<h2>Storage</h2><p class="hint" id="pistore"></p>' +
      '<label class="f">Keep finished recordings (days)</label>' + stepper('pikeep', (STORE && STORE.keep_days) || 14, 1, 1, 365) +
      '<label class="check"><input type="checkbox" id="piauto"' + (!STORE || STORE.auto ? ' checked' : '') +
      '><span>Clean up automatically when Studio starts</span></label>' +
      '<button class="btn block" type="button" id="pistoresave">Save storage settings</button>' +
      '<button class="btn block" type="button" id="piclean" style="margin-top:8px">Clean up now</button>' +
      '<button class="btn block" type="button" id="piexport" style="margin-top:8px">Copy to USB, then delete</button>';
    function showStore(j) {
      STORE = j; renderChips();
      var e = $id('pistore');
      if (e) e.textContent = j.free_gb + ' GB free of ' + j.total_gb + ' GB. Recordings: ' + j.recordings + ' (' +
        j.recordings_gb + ' GB); can be removed now: ' + j.deletable + ' (' + j.deletable_gb + ' GB); kept for unfinished jobs: ' +
        j.kept_for_unfinished_jobs + '. New scans need ' + j.min_free_gb + ' GB free.';
    }
    loadStorage().then(function (j) { if (j) showStore(j); });
    $id('pistoresave').onclick = function () {
      post('/api/pi/storage/settings', { keep_days: +$id('pikeep').value, auto: $id('piauto').checked })
        .then(function (j) { showStore(j); say('good', 'Storage settings saved.'); })
        .catch(function (e) { say('err', e.message); });
    };
    function clean(exp) {
      var n = STORE ? STORE.deletable : 0;
      if (!n) { say('info', 'Nothing to remove - no finished recordings older than ' + (STORE ? STORE.keep_days : 14) + ' days.'); return; }
      if (!confirm('Delete ' + n + ' old recordings (' + STORE.deletable_gb + ' GB)' + (exp ? ' after copying them to the USB stick' : '') +
        '? Jobs, programs, logs and calibration are kept.')) return;
      say('info', exp ? 'Copying to the USB stick, then deleting' : 'Deleting old recordings');
      post('/api/pi/storage/cleanup', { export: exp })
        .then(function (j) {
          showStore(j.storage);
          say('good', j.deleted + ' recordings removed, ' + j.freed_gb + ' GB freed' + (j.exported ? ', ' + j.exported + ' copied to ' + j.export_dir : '') +
            (j.skipped.length ? ' (' + j.skipped.length + ' skipped)' : '') + '.');
        })
        .catch(function (e) { say('err', e.message); });
    }
    $id('piclean').onclick = function () { clean(false); };
    $id('piexport').onclick = function () { clean(true); };
  }
  function renderPin() {
    var p = pane('pin');
    p.innerHTML = '<h2>PIN</h2><div id="pipinform"></div>' +
      '<button class="btn block" type="button" id="pilock2" style="margin-top:14px">Lock Admin now</button>';
    pinForm($id('pipinform'), 'pinew1', 'pinew2', false, function () { renderPin(); say('good', 'New PIN saved.'); });
    $id('pilock2').onclick = lock;
  }
  function renderServo() {
    var box = $id('piservobox');
    if (!box) return;
    var r = document.querySelector('input[name="pidisp"]:checked');
    if (!r || r.value !== 'servo_ssr') { box.innerHTML = ''; return; }
    getj('/api/pi/paste_output').then(function (v) {
      box.innerHTML = '<h3 style="margin-top:14px">Servo angles</h3><p class="hint">' + (v.ready
        ? 'Saved: closed ' + v.closed_deg + ' deg, open ' + v.open_deg + ' deg - ' + esc2(v.signal || '')
        : 'Not set yet - find the angles below and save them.') + '</p>' +
        '<label class="f">Closed - tube shut</label><div class="row" style="gap:8px">' + stepper('piclosed', v.closed_deg != null ? v.closed_deg : 90, 1, 0, 180) +
        '<button class="btn" type="button" data-try="piclosed">Try</button></div>' +
        '<label class="f">Open - paste flows</label><div class="row" style="gap:8px">' + stepper('piopen', v.open_deg != null ? v.open_deg : 60, 1, 0, 180) +
        '<button class="btn" type="button" data-try="piopen">Try</button></div>' +
        '<button class="btn primary block" type="button" id="piservosave" style="margin-top:8px">Save angles</button>';
      box.querySelectorAll('[data-try]').forEach(function (b) {
        b.onclick = function () {
          var a = +$id(b.getAttribute('data-try')).value;
          post('/api/pi/servo/try', { angle: a }).then(function () { say('good', 'Servo at ' + a + ' deg - is the tube shut or open?'); })
            .catch(function (e) { say('err', e.message); });
        };
      });
      $id('piservosave').onclick = function () {
        post('/api/pi/servo/save', { closed_deg: +$id('piclosed').value, open_deg: +$id('piopen').value })
          .then(function (w) { say('good', 'Angles saved - servo closed at ' + w.closed_deg + ' deg.'); renderServo(); })
          .catch(function (e) { say('err', e.message); });
      };
    }).catch(function (e) { box.innerHTML = '<p class="hint">' + esc2(e.message) + '</p>'; });
  }

  /* Jog in Admin: X/Y shown even when the last job opened was a real-hand job (the Pi server allows them with the
     Admin session - see pi_jog). Runs after the page's own Jog button handler, which hides them. */
  document.addEventListener('click', function (e) {
    if (!(e.target.closest && e.target.closest('#mjog')) || !admin()) return;
    document.querySelectorAll('#dlgjog [data-jog^="X"],#dlgjog [data-jog^="Y"]').forEach(function (b) { b.hidden = false; });
    if (typeof msg === 'function') msg('msgjog', '', '');
    if (typeof jogShow === 'function') jogShow();
  });

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
  if (typeof calibCardShow === 'function') {
    var origCardShow = calibCardShow;
    calibCardShow = function () { var r = origCardShow.apply(this, arguments); try { showPos(); } catch (e) { /* not built */ } return r; };
  }
  if (typeof go === 'function') {
    var origGoAdmin = go;
    go = function (view) {
      if (view !== 'calib') document.body.classList.remove('pi-admin', 'pi-daily');
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
  buildRunBar();
  loadSetup();
  loadStorage();
  setInterval(loadStorage, 30000);
  setInterval(tick, 500);
})();

/* Full-screen region editor (2026-10-17, user: "this open should come on whole screen for better selection").
   Hand & region step: "Full screen" shows the outline picture turned sideways over the whole portrait screen (the
   picture is wide, the screen is tall - about twice the size). The editor maps a touch through the picture's on-screen
   box, so while turned every touch on it is re-sent with the position the editor expects; corners land exactly where
   the finger is. "Done" puts the picture back. Nothing else changes: the same outline, the same checks. */
(function () {
  'use strict';
  var ed = document.getElementById('ed');
  if (!ed) return;
  var media = ed.closest('.media'), home = null, next = null, turned = false, over = null;
  var TYPES = ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'dblclick', 'contextmenu'];

  function build() {
    over = document.createElement('div');
    over.id = 'pifull';
    over.innerHTML = '<div class="pifullbar"><span>Drag the corners. Double-tap the line to add a corner.</span>' +
      '<button class="btn primary small" type="button" id="pifulldone">Done</button></div><div class="pifullarea" id="pifullarea"></div>';
    document.body.appendChild(over);
    document.getElementById('pifulldone').onclick = close;
  }
  function open() {
    if (turned || !media) return;
    if (!over) build();
    home = media.parentNode; next = media.nextSibling;
    var area = document.getElementById('pifullarea');
    area.appendChild(media);
    var mach = document.getElementById('mach');                          /* STOP stays visible below */
    over.style.bottom = (mach && !mach.hidden ? mach.getBoundingClientRect().height : 0) + 'px';
    over.hidden = false;
    var r = area.getBoundingClientRect();
    var w = Math.min(r.height - 8, (r.width - 8) * ed.width / ed.height);     /* turned: width runs down the screen */
    var h = w * ed.height / ed.width;
    media.classList.add('piturned');
    media.style.width = w + 'px'; media.style.height = h + 'px';
    media.style.left = (r.width - w) / 2 + 'px'; media.style.top = (r.height - h) / 2 + 'px';
    turned = true;
  }
  function close() {
    if (!turned) return;
    media.classList.remove('piturned');
    media.style.width = media.style.height = media.style.left = media.style.top = '';
    home.insertBefore(media, next);
    over.hidden = true;
    turned = false;
  }
  /* turned clockwise by 90 deg: a touch at (vx, vy) inside the picture's on-screen box is the picture point
     (x, y) = (vy, Hc - vx) in its own CSS size Wc x Hc; the editor reads (clientX - left) / boxWidth etc., so send it
     the client position that gives exactly that point. */
  function remap(e) {
    var r = ed.getBoundingClientRect();
    var Wc = r.height, Hc = r.width;                                         /* the box of the turned picture */
    var vx = e.clientX - r.left, vy = e.clientY - r.top;
    var x = vy, y = Hc - vx;                                                 /* picture point, CSS px */
    return { clientX: r.left + x / Wc * r.width, clientY: r.top + y / Hc * r.height };
  }
  TYPES.forEach(function (type) {
    window.addEventListener(type, function (e) {
      if (!turned || e.target !== ed || e.piRemapped) return;
      e.stopImmediatePropagation();
      if (type === 'contextmenu') e.preventDefault();
      var p = remap(e), init = {
        bubbles: true, cancelable: true, clientX: p.clientX, clientY: p.clientY, button: e.button, buttons: e.buttons,
        pointerId: e.pointerId, pointerType: e.pointerType, isPrimary: e.isPrimary
      };
      var ev = (type.indexOf('pointer') === 0) ? new PointerEvent(type, init) : new MouseEvent(type, init);
      Object.defineProperty(ev, 'piRemapped', { value: true });
      ed.dispatchEvent(ev);
    }, true);
  });
  var b = document.createElement('button');
  b.className = 'btn small'; b.type = 'button'; b.id = 'pifullbtn'; b.textContent = 'Full screen';
  if (media && media.parentNode) media.parentNode.insertBefore(b, media.nextSibling);
  b.onclick = open;
  window.addEventListener('keydown', function (e) { if (turned && e.key === 'Escape') close(); });
  if (typeof go === 'function') {                                            /* leaving the step puts it back */
    var origGo = go;
    go = function () { close(); return origGo.apply(this, arguments); };
  }
})();
