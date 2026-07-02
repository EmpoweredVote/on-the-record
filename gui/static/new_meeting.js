// Live new-meeting form: preview card, auto-derived meeting id, and the
// city-required toggle for deliberative kinds. Display-only — the server is
// authoritative for derivation and validation.
(function () {
  const main = document.querySelector("main.newpage");
  if (!main) return;
  const cityRequired = (main.getAttribute("data-city-required") || "").split(",").filter(Boolean);

  const $ = (id) => document.getElementById(id);
  const input = { kind: $("f-kind"), city: $("f-city"), mtype: $("f-mtype"),
                  date: $("f-date"), title: $("f-title") };

  const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  function refresh() {
    const kind = input.kind.value;
    const city = input.city.value.trim();
    const mtype = input.mtype.value.trim();
    const date = input.date.value.trim();
    const title = input.title.value.trim();

    // help text
    const kh = (window.__EVENT_KIND_HELP || {})[kind] || "";
    $("kind-help").textContent = kh;
    $("compute-help").textContent = (window.__COMPUTE_HELP || {})[$("f-compute").value] || "";
    $("diarizer-help").textContent = (window.__DIARIZER_HELP || {})[$("f-diarizer").value] || "";

    // derived meeting id: {date}-{slug(meeting_type)}
    const mid = (date && mtype) ? `${date}-${slug(mtype)}` : "—";
    $("derived-id").textContent = mid;

    // preview card
    $("pv-title").textContent = title || [city, mtype].filter(Boolean).join(" ") || "(untitled)";
    $("pv-kind").textContent = kind;
    $("pv-sub").textContent = [date].filter(Boolean).join(" · ");

    // city-required marker + native required attr
    const needCity = cityRequired.includes(kind);
    $("city-req").hidden = !needCity;
    input.city.toggleAttribute("required", needCity);
  }

  main.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  });
  refresh();
})();
