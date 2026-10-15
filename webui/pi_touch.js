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
