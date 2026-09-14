/* The home page hero: real questions rise behind the box, and every few
   seconds one lights up and its words type into the search box. A tap on a
   rising question opens that question's answer. Pool from
   /asked/pool.json, with an inline fallback.
   Bubbles are launched one at a time per column, each only once the one
   before it has cleared, so they never overlap; on a phone there is one
   lane and the bubbles alternate between the two edges like a chat. On a
   wide screen there is also a column through the middle, behind the box.
   Under prefers-reduced-motion the same questions stand still as a short
   "People asked" list under the box (UI polish pass, 2026-09-14). */
(function () {
  var field = document.getElementById('catch-field'), inp = document.getElementById('catch-q'), box = document.querySelector('.catch-box');
  if (!field || !inp) return;
  var phone = window.innerWidth < 1024;   // one lane below 1024px
  var HOLD = 5000, SPEED = phone ? 52 : 46, GAP = phone ? 90 : 240;   // px per second; clear space between bubbles
  var cur = -1, timer = null, typer = null, hov = null, P = [], order = [], k = 0, cols = [], tick = null;

  /* the question's own page: the closest questions, with this one pinned first and opened */
  function href(p) { return '/asked?q=' + encodeURIComponent(p.t) + (p.id ? '&play=' + encodeURIComponent(p.id) : ''); }
  function live() { return [].slice.call(field.querySelectorAll('.thought')); }
  /* the bubble's rise animation itself, not a transition on it (those come first in getAnimations) */
  function rise(a) { var l = a.getAnimations ? a.getAnimations() : [], i; for (i = 0; i < l.length; i++) if (l[i].animationName === 'catch-rise') return l[i]; return null; }
  /* a bubble passing behind the box is not one to light up or to lean toward */
  /* a bubble fading in or out at the edges is not one to hover or lean */
  function faint(a) { return +getComputedStyle(a).opacity < 0.3; }
  function behindBox(r) { if (phone || !box) return false; var b = box.getBoundingClientRect(); return r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top; }

  function type(text) {
    clearInterval(typer);
    if (document.activeElement === inp || inp.value) { inp.placeholder = text; return; }
    var i = 0; inp.placeholder = '';
    typer = setInterval(function () { i++; inp.placeholder = text.slice(0, i) + (i < text.length ? '▏' : ''); if (i >= text.length) clearInterval(typer); }, 36);
  }
  function show(i) {
    clearTimeout(timer); cur = (i + P.length) % P.length;
    type(P[cur].q || P[cur].t);
    timer = setTimeout(next, HOLD);
  }
  /* every few seconds one of the bubbles on screen lights up and its words type
     into the box; the bubble itself keeps rising, nothing vanishes */
  function next() {
    var pills = live().filter(function (p) { var r = p.getBoundingClientRect(); return r.top > 40 && r.bottom < innerHeight - 40 && !behindBox(r); }), pick = null;
    if (pills.length) { var p = pills[Math.floor(Math.random() * pills.length)]; pick = +p.dataset.i; p.classList.add('typing'); setTimeout(function () { p.classList.remove('typing'); }, 2600); }
    if (pick === null || pick === cur) pick = (cur + 1) % P.length;
    show(pick);
  }
  function nextIdx() { return order[k++ % order.length]; }

  /* the columns the bubbles rise in: one lane on a phone (alternating edges);
     on a wide screen as many 320px slots as fit either side of the box, and
     one through the middle, behind the box */
  function columns() {
    var fw = field.clientWidth, cs = [], i;
    if (phone) return [{ lane: true, n: 0, speed: SPEED, j: 0 }];
    var br = box.getBoundingClientRect(), fr = field.getBoundingClientRect();
    var bl = br.left - fr.left, brt = br.right - fr.left, L = bl - 12, R = fw - brt - 12;
    var kl = Math.min(2, Math.max(1, Math.floor(L / 320))), kr = Math.min(2, Math.max(1, Math.floor(R / 320)));   // at most two columns a side
    for (i = 0; i < kl; i++) cs.push({ x: 12 + i * (L / kl), w: L / kl });
    cs.push({ x: bl, w: brt - bl, mid: true });   // the middle: the thoughts rise up through the box
    for (i = 0; i < kr; i++) cs.push({ x: brt + 12 + i * (R / kr), w: R / kr });
    cs.forEach(function (c, j) { c.j = j; c.speed = SPEED * (0.86 + 0.09 * j); });   // 0.86x .. : the columns drift apart
    return cs;
  }
  /* a question that needs two or three lines gets a bubble just wide enough for
     its balanced lines, so it never carries empty space on the right */
  function tighten(a) {
    var pad = 28, w0 = a.offsetWidth, h0 = a.offsetHeight;
    a.style.whiteSpace = 'nowrap'; var natural = a.scrollWidth; a.style.whiteSpace = '';
    if (natural <= w0 + 1) return;   // already on one line
    var lines = Math.ceil((natural - pad) / (w0 - pad)), w = Math.ceil((natural - pad) / lines) + pad + 10, i;
    for (i = 0; i < 8; i++) { a.style.width = w + 'px'; if (a.offsetHeight <= h0) return; w += 12; }
    a.style.width = '';
  }
  function launch(c, delay) {   // delay < 0 starts the bubble part-way up (used to fill the field at the start)
    delay = delay || 0;
    var p = P[nextIdx()], a = document.createElement('a'); a.className = 'thought'; a.href = href(p); a.textContent = p.t; a.dataset.i = P.indexOf(p); a.dataset.c = c.j || 0;
    field.appendChild(a);   // a tap follows the link: that question's answer
    a.style.setProperty('--x', '6px');   // measure with the whole field available, not from the static position
    tighten(a);
    var pw = a.offsetWidth || 200, fw = field.clientWidth, x;
    if (c.lane) { c.n++; x = (c.n % 2) ? 8 : fw - pw - 8; } else { x = c.x + Math.max(0, (c.w - pw) / 2); }
    a.style.setProperty('--x', Math.max(6, Math.min(x, fw - pw - 6)) + 'px');
    var H = field.clientHeight + 140;   // from 70px below the field to 70px above it
    a.style.setProperty('--h', H + 'px'); a.style.setProperty('--d', (H / c.speed) + 's'); a.style.setProperty('--delay', delay + 's');
    a.addEventListener('animationend', function () { if (a.parentNode) a.parentNode.removeChild(a); });
    c.last = a; c.t0 = performance.now() + delay * 1000; c.delay = delay;
    c.gap = GAP * (0.7 + Math.random() * 0.7);   // uneven spacing, like thoughts rather than a ruler
  }
  /* the next bubble in a column may go once the last one has risen its own height plus the gap */
  function due(c) {
    if (!c.last || !c.last.parentNode) return true;
    var an = rise(c.last);   // the rise's own clock, not a guess
    var risen = an && an.currentTime != null ? c.speed * (an.currentTime / 1000 - c.delay) : c.speed * (performance.now() - c.t0) / 1000;
    return risen >= c.last.offsetHeight + (c.gap || GAP);
  }
  function drift() {
    cols = columns();
    /* fill each column at once, evenly spaced, the lowest bubble just starting */
    var step = (70 + GAP) / SPEED, m = Math.max(1, Math.floor((field.clientHeight + 140) / (70 + GAP)));
    cols.forEach(function (c, j) { var off = ((j * 0.37) % 1) * step + Math.random() * step * 0.3;   // each column starts elsewhere in its cycle
      for (var i = m - 1; i >= 0; i--) launch(c, -(i * step * (0.85 + Math.random() * 0.3) + off)); });
    tick = setInterval(function () { if (document.hidden) return; cols.forEach(function (c) { if (due(c)) launch(c); }); }, 200);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { live().forEach(function (t) { t.parentNode && t.parentNode.removeChild(t); }); }
      else { cols.forEach(function (c) { c.last = null; }); }
    });
    /* resting on a bubble: its words type into the box and the cycle waits; leaving it resumes the cycle */
    field.addEventListener('mouseover', function (e) {
      var a = e.target.closest('.thought'); if (!a || faint(a) || behindBox(a.getBoundingClientRect())) return;
      clearTimeout(timer); clearTimeout(hov);
      hov = setTimeout(function () { live().forEach(function (t) { t.classList.remove('typing'); }); a.classList.add('typing'); show(+a.dataset.i); clearTimeout(timer); }, 300);
    });
    field.addEventListener('mouseout', function (e) {
      var a = e.target.closest('.thought'); if (!a || (e.relatedTarget && a.contains(e.relatedTarget))) return;
      clearTimeout(hov); a.classList.remove('typing'); timer = setTimeout(next, 2500);
    });
    inp.addEventListener('focus', function () { clearTimeout(timer); clearInterval(typer); inp.placeholder = 'Ask a question or search a word'; });
    inp.addEventListener('blur', function () { if (!inp.value) timer = setTimeout(next, 1500); });
  }
  function start(items) {
    P = items.filter(function (p) { return p && p.t && p.y; });
    if (!P.length) return;
    order = P.map(function (_, i) { return i; }).sort(function () { return Math.random() - .5; });
    // the typed pairs lead; the rest follow in random order
    var td = order.filter(function (i) { return P[i].today; }), pairs = order.filter(function (i) { return P[i].q && !P[i].today; }), rest = order.filter(function (i) { return !P[i].q && !P[i].today; });
    order = td.concat(pairs, rest);   // today's question leads
    var still = document.getElementById('catch-still');
    /* phones (no full-height field any more) and reduced-motion: a static list */
    if (still && window.matchMedia && (window.innerWidth < 761 || matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      still.innerHTML = '<span>People asked</span>' + order.slice(0, 6).map(function (i) { return '<a href="' + esc(href(P[i])) + '">' + esc(P[i].t) + '</a>'; }).join('');
      still.hidden = false; inp.placeholder = P[order[0]].q || P[order[0]].t;
      return;
    }
    drift();
    show(order[0]);
  }
  /* the field answers the hand, like water: a soft light follows the pointer,
     the column under it slows so its questions can be read (and stands still
     while one is held), the bubbles within reach lean toward the hand, lift
     and light up, and the whole field leans a little the other way.
     Only where there is a real pointer; a phone has none. */
  (function () {
    if (phone || !(window.matchMedia && matchMedia('(pointer:fine)').matches)) return;
    var mx = -1e4, my = -1e4, raf = null, lx = 0, ly = 0, hovCol = -1, light = document.createElement('span');
    light.className = 'catch-light'; field.appendChild(light);
    field.addEventListener('mouseover', function (e) { var a = e.target.closest('.thought'); hovCol = a ? +a.dataset.c : -1; });
    field.addEventListener('mouseout', function (e) { if (e.target.closest('.thought')) hovCol = -1; });
    /* ease a bubble's pace toward the target; true while it is still changing */
    function pace(a, r) {
      var an = rise(a); if (!an) return false;
      var now = a._r == null ? 1 : a._r, nr = now + (r - now) * 0.2; if (Math.abs(nr - r) < 0.02) nr = r;
      if (nr !== now) { a._r = nr; try { an.playbackRate = nr; } catch (e) {} }   // set directly: the 'seamless' update, called every frame, never settles
      return nr !== r;
    }
    function react() {
      raf = null; var fr = field.getBoundingClientRect(), on = mx > -1e3, px = 0, py = 0, busy = false;
      var inF = on && mx >= fr.left && mx <= fr.right && my >= fr.top && my <= fr.bottom;   // the hand is over the water
      if (on) { px = (mx - fr.left) / fr.width - 0.5; py = (my - fr.top) / fr.height - 0.5; }
      field.style.translate = (-px * 16).toFixed(1) + 'px ' + (-py * 10).toFixed(1) + 'px';
      if (inF) {   // the light follows a little behind the hand
        var tx = mx - fr.left, ty = my - fr.top; lx += (tx - lx) * 0.18; ly += (ty - ly) * 0.18;
        light.style.translate = lx.toFixed(1) + 'px ' + ly.toFixed(1) + 'px'; light.style.opacity = 1;
        if (Math.abs(tx - lx) + Math.abs(ty - ly) > 0.5) busy = true;
      } else light.style.opacity = 0;
      /* each column's pace: slow under the hand, still while a thought in it is held, normal elsewhere */
      var target = cols.map(function (c, j) {
        if (!inF) return 1; if (j === hovCol) return 0;
        var cx = fr.left + c.x + c.w / 2, d = Math.abs(mx - cx), R = c.w / 2 + 90;
        return d < R ? 1 - 0.65 * (1 - d / R) : 1;
      });
      live().forEach(function (a) {
        var t = target[+a.dataset.c]; if (pace(a, t == null ? 1 : t)) busy = true;
        var r = a.getBoundingClientRect(), cx = r.left + r.width / 2 - (a._lx || 0), cy = r.top + r.height / 2 - (a._ly || 0), d = Math.hypot(cx - mx, cy - my), R = 220;
        if (inF && d < R && !behindBox(r) && !faint(a)) {
          var kk = 1 - d / R, u = d || 1, g = kk * Math.min(1, d / 24);   // the lean: toward the hand, from the bubble's own place; nil right under it
          a._lx = (mx - cx) / u * g * 10; a._ly = (my - cy) / u * g * 6;
          a.style.transform = 'translate(' + a._lx.toFixed(1) + 'px,' + a._ly.toFixed(1) + 'px) scale(' + (1 + kk * 0.07).toFixed(3) + ')';
          a.classList.add('near');
        } else if (a.classList.contains('near')) { a.style.transform = ''; a._lx = a._ly = 0; a.classList.remove('near'); }
      });
      if (busy) raf = requestAnimationFrame(react);
    }
    document.addEventListener('mousemove', function (e) { mx = e.clientX; my = e.clientY; if (!raf) raf = requestAnimationFrame(react); }, { passive: true });
    document.addEventListener('mouseleave', function () { mx = my = -1e4; hovCol = -1; if (!raf) raf = requestAnimationFrame(react); });
  })();
  /* under the box: continue what you were hearing, your seven-day path */
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function mmss(n) { n = Math.max(0, Math.round(n)); return Math.floor(n / 60) + ':' + ('0' + n % 60).slice(-2); }
  (function () {
    var el = document.getElementById('catch-more'); if (!el) return; var parts = [];
    try {
      var h = (JSON.parse(localStorage.getItem('heard') || '[]') || [])[0];
      if (h && h.k === 'qa' && h.e - h.s > 15) parts.push('<a class=cont href="/asked?q=' + encodeURIComponent(h.t) + '&play=' + encodeURIComponent(h.id) + '&t=' + h.s + '">Continue: ' + esc(h.t) + ' <small>' + mmss(h.e - h.s) + ' left</small></a>');
      var p = JSON.parse(localStorage.getItem('path') || 'null');
      if (p && p.slug) { var day = Math.min(7, Math.floor((Date.now() - (+p.start || Date.now())) / 864e5) + 1); parts.push('<a class=path href="/situations/' + encodeURIComponent(p.slug) + '/day/' + day + '">Day ' + day + ' of 7: ' + esc(p.label || '') + '</a>'); }
    } catch (e) {}
    el.innerHTML = parts.join('<span aria-hidden="true">&middot;</span>');
  })();
  var fallback = [];
  try { fallback = JSON.parse((document.getElementById('catch-fallback') || {}).textContent || '[]'); } catch (e) {}
  fetch('/asked/pool.json', { credentials: 'same-origin' })
    .then(function (r) { if (!r.ok) throw 0; return r.json(); })
    .then(function (d) { start((d.items && d.items.length) ? d.items : fallback); })
    .catch(function () { start(fallback); });
})();
