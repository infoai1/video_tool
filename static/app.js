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
  window.VT = { romanize: romanize };
})();
