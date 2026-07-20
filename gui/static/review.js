// Clip playback: clicking a clip button seeks the media. For a YouTube embed we
// reload the iframe at ?start=<seconds> (no IFrame API needed); otherwise we seek
// the shared <video>/<audio> element.
document.addEventListener("click", function (e) {
  const btn = e.target.closest(".clip");
  if (!btn) return;
  const seek = parseFloat(btn.getAttribute("data-seek"));
  if (Number.isNaN(seek)) return;

  const yt = document.getElementById("yt-player");
  if (yt) {
    const base = yt.src.split("?")[0];
    yt.src = base + "?start=" + Math.floor(seek) + "&autoplay=1";
    return;
  }

  const player = document.getElementById("player");
  if (!player) return;
  player.currentTime = seek;
  player.play();
});

// Politician link search: debounced query to the search API; each result is a
// native POST form to the link route (Post/Redirect/Get, like rename).
// Endpoints are read from the widget's data-* attributes; these literals are
// the defaults / documented contract (search endpoint and the /link route).
const SEARCH_ENDPOINT = "/api/politicians/search";
const LINK_ROUTE_SUFFIX = "/link";
(function () {
  const DEBOUNCE = 250;
  document.addEventListener("input", function (e) {
    const input = e.target;
    if (!input.matches(".link-search input")) return;
    const widget = input.closest(".link-search");
    const results = widget.querySelector(".link-results");
    const q = input.value.trim();
    clearTimeout(widget._t);
    if (q.length < 2) { results.innerHTML = ""; return; }
    widget._t = setTimeout(async () => {
      const url = (widget.getAttribute("data-search-url") || SEARCH_ENDPOINT) + "?q=" + encodeURIComponent(q);
      let data;
      try {
        const resp = await fetch(url);
        data = await resp.json();
      } catch (_) {
        results.innerHTML = '<div class="link-msg">search unavailable</div>';
        return;
      }
      // Show all results — candidates have a politician_id but no slug, and the
      // link route now accepts id-or-slug (the result form sends both).
      const results_list = data.results || [];
      if (data.error || !results_list.length) {
        results.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      let action = widget.getAttribute("data-link-action") || "";
      if (!action.endsWith(LINK_ROUTE_SUFFIX)) action += LINK_ROUTE_SUFFIX;
      results.innerHTML = results_list.map((r) => {
        const label = [r.full_name, r.office_title, r.government_name].filter(Boolean).join(" · ");
        const esc = (s) => String(s == null ? "" : s).replace(/"/g, "&quot;").replace(/</g, "&lt;");
        return (
          '<form method="post" action="' + action + '">' +
          '<input type="hidden" name="politician_slug" value="' + esc(r.politician_slug) + '">' +
          '<input type="hidden" name="politician_id" value="' + esc(r.politician_id) + '">' +
          '<button type="submit" class="link-result">' + esc(label) + "</button>" +
          "</form>"
        );
      }).join("");
    }, DEBOUNCE);
  });
})();

// HLS attach: a House Clerk (or other .m3u8) source renders as
// <video id="player" data-hls="..."> with no src. The clip seek handler above is
// unchanged — it seeks #player once media is attached.
//
// Prefer hls.js wherever MSE is available (Chrome, Firefox, Edge, desktop Safari)
// and use native HLS only as a fallback (iOS / older Safari). Order matters:
// Chrome reports canPlayType("application/vnd.apple.mpegurl") === "maybe" (truthy)
// but CANNOT actually play HLS natively — pointing <video>.src at the manifest
// stalls with MEDIA_ERR_SRC_NOT_SUPPORTED on seek. So native must never be the
// first choice.
(function () {
  const video = document.getElementById("player");
  if (!video) return;
  const src = video.getAttribute("data-hls");
  if (!src) return;

  const useNative = function () {
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src; // iOS / older Safari: real native HLS
    }
    // else: no HLS support at all; leave the empty <video>. Transcript/review
    // still work — degraded, never broken.
  };

  const script = document.createElement("script");
  script.src = "/static/hls.min.js";
  script.onload = function () {
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls();
      hls.loadSource(src);
      hls.attachMedia(video);
    } else {
      useNative();
    }
  };
  script.onerror = useNative; // vendored hls.js missing: fall back to native
  document.head.appendChild(script);
})();
