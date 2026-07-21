// Client-side library filtering: search text + Kind + Status. Instant, no reload.
(function () {
  const search = document.getElementById("lib-search");
  const kindSel = document.getElementById("lib-kind");
  const statusSel = document.getElementById("lib-status");
  const table = document.getElementById("lib-table");
  if (!table) return;  // empty library
  const rows = Array.from(table.querySelectorAll("tbody tr"));
  const emptyMsg = document.getElementById("lib-empty-filter");

  function apply() {
    const q = (search.value || "").trim().toLowerCase();
    const kind = kindSel.value;
    const status = statusSel.value;
    let visible = 0;
    rows.forEach((tr) => {
      const hay = tr.getAttribute("data-search") || "";
      const show = (!q || hay.includes(q))
        && (!kind || tr.getAttribute("data-kind") === kind)
        && (!status || tr.getAttribute("data-status") === status);
      tr.hidden = !show;
      if (show) visible++;
    });
    if (emptyMsg) emptyMsg.hidden = visible !== 0;
  }

  [search, kindSel, statusSel].forEach((el) => {
    el.addEventListener("input", apply);
    el.addEventListener("change", apply);
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
