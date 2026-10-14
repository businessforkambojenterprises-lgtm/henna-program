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
