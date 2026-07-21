// Live new-meeting form: kind-aware field gating, preview card, auto-derived
// meeting id, source-metadata autofill, and a race typeahead. Display-only —
// the server is authoritative for derivation and validation.
(function () {
  const main = document.querySelector("main.newpage");
  if (!main) return;
  const cityRequired = (main.getAttribute("data-city-required") || "").split(",").filter(Boolean);
  const FIELDS = window.__FIELDS_BY_KIND || {};

  const $ = (id) => document.getElementById(id);
  const input = { kind: $("f-kind"), city: $("f-city"), mtype: $("f-mtype"),
                  date: $("f-date"), title: $("f-title"), guest: $("f-guest"),
                  crec: $("f-crec-chamber") };

  const DEFAULTS = window.__MEETING_TYPE_DEFAULTS || {};
  const DEFAULT_VALUES = new Set(Object.values(DEFAULTS).filter(Boolean));
  // Floor labels tracking the Congressional Record chamber (added to the
  // auto-applied set so they never clobber a hand-typed label).
  ["House Floor", "Senate Floor"].forEach((v) => DEFAULT_VALUES.add(v));

  const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const raceSlug = (s) => slug(s).split("-").filter((t) => t && !["u","s","of","the"].includes(t)).join("-");

  function applyKindDefault() {
    const cur = input.mtype.value.trim();
    if (cur === "" || DEFAULT_VALUES.has(cur)) {
      // Floor: track the chamber selection; otherwise the per-kind default.
      let def = DEFAULTS[input.kind.value] || "";
      if (input.kind.value === "floor" && input.crec && input.crec.value === "senate") {
        def = "Senate Floor";
      }
      input.mtype.value = def;
    }
  }

  function applyKindFields() {
    const shown = new Set(FIELDS[input.kind.value] || []);
    main.querySelectorAll(".fieldwrap").forEach((el) => {
      el.hidden = !shown.has(el.getAttribute("data-field"));
    });
  }

  function currentLocus() {
    const kind = input.kind.value;
    const org = slug((($("f-orgs").value || "").split(",")[0] || "").trim());
    const city = slug(input.city.value.trim());
    const guest = slug((input.guest && input.guest.value || "").trim());
    const race = ($("f-race-slug").value || "").trim();
    const body = ($("f-body") && $("f-body").value || "").trim();
    if (kind === "council" || kind === "school_board") return body || city;
    if (kind === "community_meeting") return city || org;
    if (kind === "debate" || kind === "forum") return race || org || city;
    if (kind === "news_clip" || kind === "press_conference" || kind === "podcast")
      return [guest, race].filter(Boolean).join("-") || org;
    if (kind === "floor") return "";
    return city || org;
  }

  function refresh() {
    const kind = input.kind.value;
    const mtype = input.mtype.value.trim();
    const date = input.date.value.trim();
    const title = input.title.value.trim();
    const city = input.city.value.trim();

    $("kind-help").textContent = (window.__EVENT_KIND_HELP || {})[kind] || "";
    $("compute-help").textContent = (window.__COMPUTE_HELP || {})[$("f-compute").value] || "";
    $("diarizer-help").textContent = (window.__DIARIZER_HELP || {})[$("f-diarizer").value] || "";

    // derived id: {date}-{locus}-{label}, with the label-contains-locus de-dup
    const label = slug(mtype);
    // Mirror the server's _overlaps(): whole-hyphen-token containment, so a
    // locus is only dropped when it repeats a full token of the label (not a
    // partial-word substring like "ann" inside "annual").
    const overlaps = (a, b) => `-${b}-`.includes(`-${a}-`) || `-${a}-`.includes(`-${b}-`);
    let locus = currentLocus();
    if (locus && overlaps(locus, label)) locus = "";
    const parts = [date, locus, label].filter(Boolean);
    $("derived-id").textContent = (date && label) ? parts.join("-") : "—";

    $("pv-title").textContent = title || [city, mtype].filter(Boolean).join(" ") || "(untitled)";
    $("pv-kind").textContent = kind;
    $("pv-sub").textContent = [date].filter(Boolean).join(" · ");

    const needCity = cityRequired.includes(kind);
    $("city-req").hidden = !needCity;
    input.city.toggleAttribute("required", needCity);
  }

  // --- Race typeahead (mirrors the review link-search) ---
  const raceWidget = $("f-race");
  const raceInput = $("f-race-input");
  const raceResults = $("f-race-results");
  const raceChosen = $("f-race-chosen");
  let raceTimer = null;
  function chooseRace(id, sslug, labelText) {
    $("f-race-id").value = id;
    $("f-race-slug").value = sslug || raceSlug(labelText);
    raceChosen.hidden = false;
    raceChosen.textContent = "✓ " + labelText + " (clear)";
    raceResults.innerHTML = "";
    raceInput.value = "";
    refresh();
  }
  if (raceChosen) raceChosen.addEventListener("click", () => {
    $("f-race-id").value = ""; $("f-race-slug").value = "";
    raceChosen.hidden = true; raceChosen.textContent = ""; refresh();
  });
  if (raceInput) raceInput.addEventListener("input", () => {
    const q = raceInput.value.trim();
    clearTimeout(raceTimer);
    if (q.length < 2) { raceResults.innerHTML = ""; return; }
    raceTimer = setTimeout(async () => {
      const url = (raceWidget.getAttribute("data-search-url") || "/api/races/search") + "?q=" + encodeURIComponent(q);
      let data;
      try { data = await (await fetch(url)).json(); }
      catch (_) { raceResults.innerHTML = '<div class="link-msg">search unavailable</div>'; return; }
      const list = data.results || [];
      if (data.error || !list.length) {
        raceResults.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      raceResults.innerHTML = "";
      list.forEach((r) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "link-result"; b.textContent = r.label;
        b.addEventListener("click", () => chooseRace(r.race_id, r.slug, r.label));
        raceResults.appendChild(b);
      });
    }, 250);
  });

  // --- Source metadata autofill (unchanged from the prior version) ---
  const sourceInput = $("f-input");
  const note = $("source-meta-note");
  let lastFetched = null;
  const looksLikeUrl = (s) => /^https?:\/\//i.test(s.trim());
  const fillIfEmpty = (el, value) => { if (el && value && el.value.trim() === "") el.value = value; };
  async function fetchSourceMeta() {
    const url = sourceInput.value.trim();
    if (!looksLikeUrl(url) || url === lastFetched) return;
    lastFetched = url;
    note.textContent = "Fetching video details…";
    try {
      const resp = await fetch("/api/source-meta?url=" + encodeURIComponent(url));
      if (!resp.ok) throw new Error("bad status");
      const data = await resp.json();
      if (!data.date && !data.title && !data.event_org) { note.textContent = ""; return; }
      fillIfEmpty(input.date, data.date);
      fillIfEmpty(input.title, data.title);
      fillIfEmpty($("f-orgs"), data.event_org);
      note.textContent = "";
      refresh();
    } catch (e) { note.textContent = "Couldn't fetch details — fill in manually."; }
  }
  sourceInput.addEventListener("blur", fetchSourceMeta);
  sourceInput.addEventListener("change", fetchSourceMeta);
  sourceInput.addEventListener("paste", () => setTimeout(fetchSourceMeta, 0));

  main.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  });
  input.kind.addEventListener("change", () => { applyKindDefault(); applyKindFields(); refresh(); });
  if (input.crec) input.crec.addEventListener("change", () => { applyKindDefault(); refresh(); });
  applyKindDefault();
  applyKindFields();
  refresh();
})();
