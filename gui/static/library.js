// Client-side library: search + Kind + Status + date range + quick chips,
// clickable rows, and sortable columns. Instant, no reload.
(function () {
  const search = document.getElementById("lib-search");
  const kindSel = document.getElementById("lib-kind");
  const statusSel = document.getElementById("lib-status");
  const dateFrom = document.getElementById("lib-date-from");
  const dateTo = document.getElementById("lib-date-to");
  const table = document.getElementById("lib-table");
  if (!table) return;  // empty library
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  const emptyMsg = document.getElementById("lib-empty-filter");

  function apply() {
    const q = (search.value || "").trim().toLowerCase();
    const kind = kindSel.value;
    const status = statusSel.value;
    const from = (dateFrom && dateFrom.value) || "";
    const to = (dateTo && dateTo.value) || "";
    let visible = 0;
    rows.forEach((tr) => {
      const hay = tr.getAttribute("data-search") || "";
      const d = tr.getAttribute("data-date") || "";
      const show = (!q || hay.includes(q))
        && (!kind || tr.getAttribute("data-kind") === kind)
        && (!status || tr.getAttribute("data-status") === status)
        && (!from || (d && d >= from))          // ISO YYYY-MM-DD compares lexically
        && (!to || (d && d <= to));
      tr.hidden = !show;
      if (show) visible++;
    });
    if (emptyMsg) emptyMsg.hidden = visible !== 0;
  }

  [search, kindSel, statusSel, dateFrom, dateTo].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", apply);
    el.addEventListener("change", apply);
  });

  // Quick-filter chips: set the Status select (or clear it) and re-apply.
  document.querySelectorAll(".lib-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const v = chip.getAttribute("data-chip");
      statusSel.value = v === "all" ? "" : v;
      apply();
    });
  });

  // Column sorting: click a th[data-sort] to sort; click again to reverse.
  const NUMERIC = new Set(["speakers", "length"]);
  let sortKey = null, sortDir = 1;
  function cellVal(tr, key) {
    const map = { date: "data-date", kind: "data-kind", speakers: "data-speakers",
                  length: "data-length", status: "data-status" };
    return tr.getAttribute(map[key] || "") || "";
  }
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort");
      sortDir = (sortKey === key) ? -sortDir : 1;
      sortKey = key;
      const sorted = rows.slice().sort((a, b) => {
        let av = cellVal(a, key), bv = cellVal(b, key);
        if (NUMERIC.has(key)) { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; return (av - bv) * sortDir; }
        return String(av).localeCompare(String(bv)) * sortDir;
      });
      sorted.forEach((tr) => tbody.appendChild(tr));   // reorder in place
    });
  });

  // Whole-row click navigates to the meeting (ignore clicks on interactive els).
  tbody.addEventListener("click", (e) => {
    if (e.target.closest("a, button, input, select, form, label")) return;
    const tr = e.target.closest("tr");
    const mid = tr && tr.getAttribute("data-meeting-id");
    if (mid) location.href = "/meetings/" + encodeURIComponent(mid);
  });
})();

// Live batch view: poll /batch/status while anything is in flight, updating the
// counts, the pending strip, and the status cell of each running row in place.
(function () {
  const header = document.getElementById("batch-header");
  if (!header) return;
  const runCount = document.getElementById("batch-running-count");
  const pendCount = document.getElementById("batch-pending-count");
  const strip = document.getElementById("pending-strip");

  function renderPending(pending) {
    strip.innerHTML = (pending || []).map((p) => {
      const label = String(p.label == null ? "" : p.label)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
      return '<span class="pending-chip">' + label +
        '<form method="post" action="/batch/pending/' + p.pending_id + '/remove">' +
        '<button type="submit" title="Remove from queue">✕</button></form></span>';
    }).join("");
  }

  async function poll() {
    let st;
    try { st = await (await fetch("/batch/status")).json(); }
    catch (_) { return setTimeout(poll, 4000); }
    if (runCount) runCount.textContent = st.counts.running;
    if (pendCount) pendCount.textContent = st.counts.pending;
    renderPending(st.pending);
    (st.running || []).forEach((r) => {
      const row = document.querySelector('tr[data-meeting-id="' + r.meeting_id + '"]');
      const cell = row && row.querySelector(".status-cell .stage");
      if (cell) cell.textContent = r.stage_label;
    });
    if (st.counts.running > 0 || st.counts.pending > 0) setTimeout(poll, 4000);
  }
  poll();
})();
