// On-demand Roman Urdu: any `.roman[data-seg]` that is still empty gets filled
// by asking the server to transliterate that segment (cached server-side, so
// each segment is done at most once). The page shows Urdu immediately; Roman
// streams in a few lines at a time.
(function () {
  async function romanize() {
    var pending = Array.prototype.filter.call(
      document.querySelectorAll(".roman[data-seg]"),
      function (el) { return !el.textContent.trim(); }
    );
    if (!pending.length) return;
    var byId = {};
    pending.forEach(function (el) { byId[el.getAttribute("data-seg")] = el; });
    var ids = pending.map(function (el) { return +el.getAttribute("data-seg"); });

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
        return; // leave the Urdu in place if transliteration is unavailable
      }
    }
  }
  document.addEventListener("DOMContentLoaded", romanize);
})();
