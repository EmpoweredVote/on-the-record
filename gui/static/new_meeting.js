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

  const DEFAULTS = window.__MEETING_TYPE_DEFAULTS || {};
  const DEFAULT_VALUES = new Set(Object.values(DEFAULTS).filter(Boolean));

  function applyKindDefault() {
    const cur = input.mtype.value.trim();
    // Only auto-fill when the field is empty or still holds an auto-applied
    // default — never clobber a label the user typed.
    if (cur === "" || DEFAULT_VALUES.has(cur)) {
      const def = DEFAULTS[input.kind.value] || "";
      input.mtype.value = def;
    }
  }

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

  const sourceInput = $("f-input");
  const note = $("source-meta-note");
  let lastFetched = null;

  const looksLikeUrl = (s) => /^https?:\/\//i.test(s.trim());

  function fillIfEmpty(el, value) {
    if (el && value && el.value.trim() === "") el.value = value;
  }

  async function fetchSourceMeta() {
    const url = sourceInput.value.trim();
    if (!looksLikeUrl(url) || url === lastFetched) return;
    lastFetched = url;
    note.textContent = "Fetching video details…";
    try {
      const resp = await fetch("/api/source-meta?url=" + encodeURIComponent(url));
      if (!resp.ok) throw new Error("bad status");
      const data = await resp.json();
      if (!data.date && !data.title && !data.event_org) {
        note.textContent = "";  // non-video URL or nothing to fill
        return;
      }
      fillIfEmpty(input.date, data.date);
      fillIfEmpty(input.title, data.title);
      fillIfEmpty($("f-orgs"), data.event_org);
      note.textContent = "";
      refresh();
    } catch (e) {
      note.textContent = "Couldn't fetch details — fill in manually.";
    }
  }

  sourceInput.addEventListener("blur", fetchSourceMeta);
  sourceInput.addEventListener("change", fetchSourceMeta);
  // Fire on paste too, so fields fill immediately without needing to blur.
  // The pasted text isn't in .value until after the event, so defer a tick.
  sourceInput.addEventListener("paste", () => setTimeout(fetchSourceMeta, 0));

  main.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  });
  input.kind.addEventListener("change", () => { applyKindDefault(); refresh(); });
  applyKindDefault();
  refresh();
})();
