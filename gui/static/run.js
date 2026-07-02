// Poll the run status and update the stepper + log until the process exits.
(function () {
  const main = document.querySelector("main[data-meeting-id]");
  if (!main) return;
  const id = main.getAttribute("data-meeting-id");
  const logEl = document.getElementById("log");
  const stateEl = document.getElementById("run-state");
  const stepper = document.getElementById("stepper");
  const errBanner = document.getElementById("error-banner");
  const reviewLink = document.getElementById("review-link");

  async function tick() {
    let st;
    try {
      const resp = await fetch(`/meetings/${encodeURIComponent(id)}/run/status`);
      if (!resp.ok) { stateEl.textContent = "Run not found."; return; }
      st = await resp.json();
    } catch (_) { setTimeout(tick, 2000); return; }

    if (st.log_tail) logEl.textContent = st.log_tail;
    logEl.scrollTop = logEl.scrollHeight;

    stepper.querySelectorAll("li").forEach((li) => {
      const s = parseInt(li.getAttribute("data-stage"), 10);
      li.classList.toggle("done", s <= st.completed_stage);
      li.classList.toggle("current", s === st.completed_stage + 1 && st.running);
    });

    if (st.running) {
      stateEl.textContent = `Running — stage ${st.completed_stage}/7 (${st.stage_label})`;
      setTimeout(tick, 1500);
    } else if (st.exit_code === 0 || st.completed_stage >= 5) {
      stateEl.textContent = "Done.";
      reviewLink.hidden = false;
    } else if (st.exit_code != null && st.exit_code !== 0) {
      stateEl.textContent = "Failed.";
      errBanner.hidden = false;
      errBanner.textContent = `Process exited with code ${st.exit_code}. See log below.`;
    } else {
      stateEl.textContent = "Idle.";
      reviewLink.hidden = false;
    }
  }
  tick();
})();
