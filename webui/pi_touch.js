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
   - Admin (PIN; the Pi server refuses admin routes without the session token): Machine and calibration, Paste
     setup (dispenser + delays, test), Servo angles, Change PIN, Lock. Forgotten PIN: raspberry_pi/admin_pin_reset.py.
   - Program step: shows the Admin's paste setup; the server applies it to every program.
   - Bottom bar: Pause / Resume next to STOP while a program runs.
   The same Studio routes and checks as before - nothing here moves the machine on its own. */
(function () {
  'use strict';
  var ADMIN_TOKEN = null, SETUP = null, DISP = null;
  var ADMIN_RE = /^\/api\/(calibration\/|machines\/(select|add|recalibrate|setup_area|setup_scan)$|pi\/servo\/|pi\/paste_setup$)/;
  function $id(id) { return document.getElementById(id); }
  function esc2(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  /* every admin request carries the session token */
  var realFetch = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    try {
      var path = new URL(String(url), location.href).pathname;
      if (ADMIN_TOKEN && opts && opts.method === 'POST' && ADMIN_RE.test(path)) {
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
    $id('pidaily').onclick = function () {                  /* operators run it; the reference stays under Admin */
      if (typeof machineBusy === 'function' && machineBusy()) return;
      go('calib');
      var b = $id('calcheck');
      if (b && b.scrollIntoView) setTimeout(function () { b.scrollIntoView({ block: 'center' }); }, 300);
    };
    var top = $id('btnjobs');
    if (top && !$id('pihomebtn')) {
      var b = document.createElement('button');
      b.className = 'btn small'; b.id = 'pihomebtn'; b.type = 'button'; b.textContent = 'Home';
      b.onclick = function () { if (typeof machineBusy === 'function' && machineBusy()) return; go('welcome'); };
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

  /* ---------- Admin ---------- */
  var dlg = null;
  function stepper(id, value, step, min, max) {
    return '<div class="pistep" data-id="' + id + '" data-step="' + step + '" data-min="' + min + '" data-max="' + max + '">' +
      '<button type="button" class="btn" data-d="-1" aria-label="Less">-</button>' +
      '<input id="' + id + '" type="number" inputmode="decimal" value="' + value + '" step="' + step + '" min="' + min + '" max="' + max + '">' +
      '<button type="button" class="btn" data-d="1" aria-label="More">+</button></div>';
  }
  function say(kind, text) { var m = $id('piadminmsg'); if (m && typeof msg === 'function') msg('piadminmsg', kind, text); }
  function openAdmin() {
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.id = 'piadmin';
      dlg.innerHTML = '<div class="dlg"><div class="row" style="justify-content:space-between;align-items:center">' +
        '<h2>Admin</h2><button class="btn small" type="button" id="piadminclose">Close</button></div>' +
        '<div id="piadminbody"></div><div class="msg" id="piadminmsg"></div></div>';
      document.body.appendChild(dlg);
      $id('piadminclose').onclick = function () { dlg.close(); };
      dlg.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('.pistep [data-d]');
        if (!b) return;
        var box = b.parentNode, inp = $id(box.getAttribute('data-id'));
        var st = +box.getAttribute('data-step'), v = (+inp.value || 0) + st * (+b.getAttribute('data-d'));
        v = Math.min(+box.getAttribute('data-max'), Math.max(+box.getAttribute('data-min'), v));
        inp.value = st < 1 ? v.toFixed(2) : Math.round(v);
      });
    }
    say('', '');
    if (!dlg.open) dlg.showModal();
    post('/api/pi/admin/status', { token: ADMIN_TOKEN }).then(function (st) {
      if (!st.unlocked) ADMIN_TOKEN = null;
      if (!st.pin_set) showSetPin(true);
      else if (!st.unlocked) showUnlock(st);
      else showPanel();
    }).catch(function (e) { say('err', e.message); });
  }
  function pinInput(id, label) {
    return '<label class="f" for="' + id + '">' + label + '</label>' +
      '<input id="' + id + '" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off">';
  }
  function showSetPin(first) {
    $id('piadminbody').innerHTML = '<p class="sub">' + (first ? 'Set a 4-digit admin PIN for this machine. Keep it safe: '
      + 'a forgotten PIN can only be reset at the Pi itself.' : 'Choose a new 4-digit PIN.') + '</p>' +
      pinInput('pipin1', 'New PIN') + pinInput('pipin2', 'Repeat the PIN') +
      '<button class="btn primary block" type="button" id="pipinsave" style="margin-top:10px">Save PIN</button>' +
      (first ? '' : '<button class="btn block" type="button" id="pipinback" style="margin-top:6px">Back</button>');
    $id('pipinsave').onclick = function () {
      var a = $id('pipin1').value, b = $id('pipin2').value;
      if (!/^\d{4}$/.test(a)) { say('err', 'The PIN must be 4 digits.'); return; }
      if (a !== b) { say('err', 'The two PINs are different.'); return; }
      post('/api/pi/admin/set_pin', { pin: a, token: ADMIN_TOKEN }).then(function (j) {
        ADMIN_TOKEN = j.token; say('good', 'PIN saved.'); showPanel();
      }).catch(function (e) { say('err', e.message); });
    };
    if (!first) $id('pipinback').onclick = showPanel;
    $id('pipin1').focus();
  }
  function showUnlock(st) {
    $id('piadminbody').innerHTML = '<p class="sub">Enter the admin PIN.</p>' + pinInput('pipin', 'PIN') +
      '<button class="btn primary block" type="button" id="piunlock" style="margin-top:10px">Unlock</button>';
    if (st && st.blocked_s) say('err', 'Too many wrong PINs - wait ' + st.blocked_s + ' s.');
    $id('piunlock').onclick = function () {
      post('/api/pi/admin/unlock', { pin: $id('pipin').value }).then(function (j) {
        ADMIN_TOKEN = j.token; say('', ''); showPanel();
      }).catch(function (e) { say('err', e.message); $id('pipin').value = ''; });
    };
    $id('pipin').focus();
  }
  function showPanel() {
    var s = SETUP || { dispenser: 'none' };
    var d = (DISP && DISP[s.dispenser]) || { start_s: 0.3, stop_s: 0.2 };
    var start = s.start_s != null ? s.start_s : d.start_s, stop = s.stop_s != null ? s.stop_s : d.stop_s;
    function radio(v, label) {
      return '<label class="check"><input type="radio" name="pidisp" value="' + v + '"' + (s.dispenser === v ? ' checked' : '') +
        '><span>' + label + '</span></label>';
    }
    $id('piadminbody').innerHTML =
      '<button class="btn block" type="button" id="pimachine">Machine and calibration</button>' +
      '<h3 style="margin-top:14px">Paste</h3>' + radio('none', 'None - hover only') +
      radio('pneumatic', 'Pneumatic air valve (DLC32 TTL)') + radio('servo_ssr', 'Servo pinch valve (this Pi)') +
      '<label class="f">Start delay (s)</label>' + stepper('pistart', start, 0.05, 0, 3) +
      '<label class="f">Stop delay (s)</label>' + stepper('pistop', stop, 0.05, 0, 3) +
      '<div class="row" style="gap:8px;margin-top:8px"><button class="btn primary" type="button" id="pisetupsave" style="flex:1">Save paste</button>' +
      '<button class="btn" type="button" id="pitest" style="flex:1">Test 1 s</button></div>' +
      '<div id="piservobox"></div>' +
      '<div class="row" style="gap:8px;margin-top:14px"><button class="btn" type="button" id="pipinchange" style="flex:1">Change PIN</button>' +
      '<button class="btn" type="button" id="pilock" style="flex:1">Lock</button></div>';
    $id('pimachine').onclick = function () {
      if (typeof machineBusy === 'function' && machineBusy()) { say('err', 'Wait until the machine has finished.'); return; }
      dlg.close(); go('calib');
    };
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
    $id('pipinchange').onclick = function () { showSetPin(false); };
    $id('pilock').onclick = function () {
      post('/api/pi/admin/lock', { token: ADMIN_TOKEN }).catch(function () {});
      ADMIN_TOKEN = null; dlg.close();
    };
    renderServo();
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

  buildHome();
  buildRunBar();
  loadSetup();
  setInterval(tick, 500);
})();
