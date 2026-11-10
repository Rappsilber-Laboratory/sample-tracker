// Copy status page: polls /copy-status/live every 5 s while the page is visible.
// Updates the heartbeat line and the live progress bar. When the per-status
// counts change (a file finished, a new file appeared) the table is reloaded,
// unless the user is typing in a field.
(function () {
  const page = document.getElementById("copy-page");
  if (!page) return;
  const liveUrl = page.dataset.liveUrl;
  let counts = JSON.parse(page.dataset.counts || "null");

  const availability = JSON.parse(page.dataset.availability || "null");

  const el = (id) => document.getElementById(id);

  function heartbeatText(s) {
    switch (s.health) {
      case "unknown": return "The copy service has not reported yet (no status.json in the cache folder).";
      case "running": return `Copy service is running now on ${s.host}.`;
      case "error": return `Last run on ${s.host} failed ${s.minutes_ago} min ago: ${s.message || ""}`;
      case "stale": return `No run for ${s.minutes_ago} min. Is the scheduled task on ${s.host} still active?`;
      default:
        return `Last run on ${s.host} ${s.minutes_ago} min ago` +
          (s.next_in_minutes != null ? ` · next run in ≈ ${s.next_in_minutes} min.` : ".");
    }
  }

  function render(data) {
    if (availability && data.availability &&
        JSON.stringify(availability) !== JSON.stringify(["astral", "zoom", "raptor"].map((key) => data.availability[key]))) {
      window.location.reload();
      return;
    }
    const s = data.service;
    const hb = el("copy-heartbeat");
    hb.className = `copy-heartbeat copy-health-${s.health}`;
    el("copy-heartbeat-text").textContent = heartbeatText(s);

    const cur = s.current;
    el("copy-current").hidden = !cur;
    if (cur) {
      el("copy-current-file").textContent = cur.file || "";
      el("copy-current-phase").textContent = cur.phase || "";
      el("copy-current-speed").textContent = cur.speed || "";
      el("copy-current-bar").style.width = `${cur.percent || 0}%`;
      const row = document.querySelector(`tr[data-file-id="${cur.file_id}"] [data-live-bar]`);
      if (row) row.style.width = `${cur.percent || 0}%`;
    }

    if (counts && data.counts && JSON.stringify(counts) !== JSON.stringify(data.counts)) {
      const busy = document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
      if (!busy) window.location.reload();
    }
  }

  async function poll() {
    if (document.visibilityState === "visible") {
      try {
        const response = await fetch(liveUrl, { headers: { Accept: "application/json" } });
        if (response.ok) render(await response.json());
      } catch (_) { /* network hiccup: try again next tick */ }
    }
    setTimeout(poll, 5000);
  }
  setTimeout(poll, 5000);
})();
