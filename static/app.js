// On-demand Roman Urdu. Any `.roman[data-seg]` that is still empty gets filled
// by asking the server to transliterate that segment (cached server-side, so
// each segment is done at most once). Exposed as VT.romanize() so each page
// decides WHEN to run it — search results fill automatically (few lines), a
// video page fills on a button press (could be hundreds of lines).
(function () {
  async function romanize(onProgress) {
    var pending = Array.prototype.filter.call(
      document.querySelectorAll(".roman[data-seg]"),
      function (el) { return !el.textContent.trim(); }
    );
    var total = pending.length;
    if (!total) { if (onProgress) onProgress(0, 0, false); return; }

    var byId = {};
    pending.forEach(function (el) { byId[el.getAttribute("data-seg")] = el; });
    var ids = pending.map(function (el) { return +el.getAttribute("data-seg"); });

    var done = 0;
    for (var i = 0; i < ids.length; i += 25) {
      var chunk = ids.slice(i, i + 25);
      try {
        var resp = await fetch("/api/romanize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: chunk }),
        });
        var data = await resp.json();
        var roman = data.roman || {};
        Object.keys(roman).forEach(function (id) {
          if (byId[id]) byId[id].textContent = roman[id] || "—";
        });
      } catch (e) {
        if (onProgress) onProgress(done, total, true);
        return;
      }
      done += chunk.length;
      if (onProgress) onProgress(done, total, false);
    }
  }
  // Wrap occurrences of any term in <mark>. Reads textContent (so it's safe to
  // re-run) and only rewrites when there's a match.
  function highlight(el, terms) {
    if (!el || !terms || !terms.length) return;
    var txt = el.textContent;
    if (!txt) return;
    var re = new RegExp(
      '(' + terms.map(function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|') + ')',
      'gi'
    );
    if (re.test(txt)) el.innerHTML = txt.replace(re, '<mark>$1</mark>');
  }

  // Romanize a specific set of segment ids (not the whole page), then run `after`.
  async function romanizeIds(ids, byId, after) {
    for (var i = 0; i < ids.length; i += 25) {
      var chunk = ids.slice(i, i + 25);
      try {
        var r = await fetch('/api/romanize', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: chunk }),
        });
        var data = await r.json();
        var roman = data.roman || {};
        Object.keys(roman).forEach(function (id) { if (byId[id]) { byId[id].textContent = roman[id] || '—'; if (after) after(byId[id]); } });
      } catch (e) { return; }
    }
  }

  // Romanize a whole video in the background, reporting % via onProgress.
  // onStart (optional) gets the initial job info ({pending, eta_minutes, …})
  // so the page can show a tentative time. Resolves when done (or immediately
  // if already romanized).
  async function romanizeVideo(videoId, onProgress, onStart) {
    var r = await fetch('/api/romanize_video', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId }),
    });
    var j = await r.json();
    if (!j.ok) throw new Error(j.error || 'could not start');
    if (j.status === 'done' || !j.job_id) { if (onProgress) onProgress(100); return; }
    if (onStart) onStart(j);
    var jid = j.job_id;
    return new Promise(function (resolve, reject) {
      (function poll() {
        fetch('/api/job/' + jid).then(function (r) { return r.json(); }).then(function (job) {
          if (job.status === 'error') { reject(new Error(job.detail || 'error')); return; }
          if (onProgress) onProgress(job.progress || 0);
          if (job.status === 'done') { resolve(); return; }
          setTimeout(poll, 2000);
        }).catch(function () { setTimeout(poll, 3000); });
      })();
    });
  }

  // small shared helpers: browser notifications + programmatic file download
  function askNotify() {
    try {
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    } catch (e) {}
  }
  function notify(title, body) {
    try {
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, body ? { body: body } : undefined);
      }
    } catch (e) {}
  }
  function download(url) {
    var a = document.createElement('a');
    a.href = url; document.body.appendChild(a); a.click(); a.remove();
  }

  window.VT = { romanize: romanize, highlight: highlight, romanizeIds: romanizeIds,
                romanizeVideo: romanizeVideo, askNotify: askNotify, notify: notify, download: download };
})();

// Sticky offsets can't be hard-coded: the top bar wraps to two rows on phones
// and the pinned player's height depends on screen width. Measure the real
// heights and expose them as CSS vars — --topbar-h anchors the pinned player /
// stage, --pin-top anchors toolbars that must sit BELOW the pinned player.
(function () {
  function px(n) { return Math.ceil(n) + 'px'; }
  function measure() {
    var root = document.documentElement.style;
    var bar = document.querySelector('.topbar');
    var barH = bar ? bar.getBoundingClientRect().height : 0;
    root.setProperty('--topbar-h', px(barH));
    var pin = barH;
    var pc = document.getElementById('player-card');       // search page player
    if (pc && !pc.hidden) pin += pc.getBoundingClientRect().height + 12;
    var stage = document.querySelector('.stage');           // video page player+tools
    if (stage) pin = barH + stage.getBoundingClientRect().height;
    root.setProperty('--pin-top', px(pin));
  }
  window.VTMeasure = measure;
  window.addEventListener('resize', measure);
  window.addEventListener('load', measure);
  document.addEventListener('DOMContentLoaded', measure);
  measure();
  var pc = document.getElementById('player-card');
  if (pc && window.MutationObserver) {
    new MutationObserver(measure).observe(pc, { attributes: true, attributeFilter: ['hidden'] });
  }
})();
