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
