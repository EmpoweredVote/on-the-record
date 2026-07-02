// Clip playback: clicking a clip button seeks the shared media element and plays.
document.addEventListener("click", function (e) {
  const btn = e.target.closest(".clip");
  if (!btn) return;
  const player = document.getElementById("player");
  if (!player) return;
  const seek = parseFloat(btn.getAttribute("data-seek"));
  if (!Number.isNaN(seek)) {
    player.currentTime = seek;
    player.play();
  }
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
      if (data.error || !data.results.length) {
        results.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      let action = widget.getAttribute("data-link-action") || "";
      if (!action.endsWith(LINK_ROUTE_SUFFIX)) action += LINK_ROUTE_SUFFIX;
      results.innerHTML = data.results.map((r) => {
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
