(() => {
  const form = document.getElementById("search-form");
  const queryInput = document.getElementById("query");
  const resourceType = document.getElementById("resource-type");
  const dateFrom = document.getElementById("date-from");
  const dateTo = document.getElementById("date-to");
  const resultsEl = document.getElementById("results");
  const emptyEl = document.getElementById("empty");
  const loadingEl = document.getElementById("loading");
  const metaEl = document.getElementById("meta");
  const healthPill = document.getElementById("health-pill");
  const overlay = document.getElementById("overlay");
  const drawer = document.getElementById("drawer");
  const drawerClose = document.getElementById("drawer-close");
  const drawerTitle = document.getElementById("drawer-title");
  const drawerMrn = document.getElementById("drawer-mrn");
  const drawerSummary = document.getElementById("drawer-summary");
  const drawerDisclaimer = document.getElementById("drawer-disclaimer");
  const drawerResources = document.getElementById("drawer-resources");

  let activeIndex = -1;
  let currentResults = [];

  function badgeClass(type) {
    if (type === "DiagnosticReport") return "bg-sand/20 text-ink";
    if (type === "PatientSummary") return "bg-tide/10 text-tideDark";
    return "bg-mist text-ink/80";
  }

  function setLoading(on) {
    loadingEl.classList.toggle("hidden", !on);
    if (on) emptyEl.classList.add("hidden");
  }

  function renderResults(payload) {
    currentResults = payload.results || [];
    activeIndex = currentResults.length ? 0 : -1;
    resultsEl.innerHTML = "";
    metaEl.textContent = currentResults.length
      ? `${currentResults.length} matches · ${payload.took_ms} ms`
      : `0 matches · ${payload.took_ms} ms`;

    if (!currentResults.length) {
      emptyEl.classList.remove("hidden");
      emptyEl.innerHTML = `<p class="font-medium">No matching media</p><p class="mt-1 text-sm text-ink/60">Broaden the query or clear filters.</p>`;
      return;
    }

    emptyEl.classList.add("hidden");
    currentResults.forEach((hit, idx) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("id", `result-${idx}`);
      li.setAttribute("aria-selected", idx === 0 ? "true" : "false");
      li.tabIndex = -1;
      li.className =
        "cursor-pointer border border-ink/10 bg-white/90 p-4 transition hover:border-tide/40 focus-within:border-tide";
      const title = hit.title || hit.resource_type || "Clinical record";
      const body = hit.snippet || "";
      li.innerHTML = `
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p class="font-semibold">${escapeHtml(hit.patient_name || "Unknown patient")}</p>
            <p class="text-xs text-ink/55">${escapeHtml(hit.mrn || "")}</p>
          </div>
          <div class="text-right">
            <span class="inline-block px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${badgeClass(hit.resource_type)}">${escapeHtml(hit.resource_type)}</span>
            <p class="mt-1 text-xs text-ink/55">${escapeHtml((hit.recorded_at || "").slice(0, 10) || "n/a")}</p>
          </div>
        </div>
        <p class="mt-2 text-sm font-medium text-ink">${escapeHtml(title)}</p>
        <p class="mt-1 text-sm text-ink/75">${escapeHtml(body)}</p>
        <div class="mt-3 flex items-center gap-3">
          <div class="score-bar w-28" aria-hidden="true"><span style="width:${Math.round((hit.relevance_score || 0) * 100)}%"></span></div>
          <span class="text-xs font-medium text-ink/60">Relevance ${(hit.relevance_score || 0).toFixed(2)}</span>
        </div>
      `;
      li.addEventListener("click", () => openPatient(hit.mrn, hit.patient_name));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openPatient(hit.mrn, hit.patient_name);
        }
      });
      resultsEl.appendChild(li);
    });
    highlightActive();
  }

  function highlightActive() {
    [...resultsEl.children].forEach((el, idx) => {
      const on = idx === activeIndex;
      el.setAttribute("aria-selected", on ? "true" : "false");
      el.classList.toggle("ring-2", on);
      el.classList.toggle("ring-tide", on);
    });
  }

  async function doSearch(event) {
    if (event) event.preventDefault();
    const query = queryInput.value.trim();
    if (!query) return;

    setLoading(true);
    resultsEl.innerHTML = "";
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          top_k: 5,
          resource_type: resourceType.value || null,
          date_from: dateFrom.value || null,
          date_to: dateTo.value || null,
        }),
      });
      if (!res.ok) throw new Error(`Search failed (${res.status})`);
      const payload = await res.json();
      renderResults(payload);
    } catch (err) {
      emptyEl.classList.remove("hidden");
      emptyEl.innerHTML = `<p class="font-medium text-alert">Search error</p><p class="mt-1 text-sm">${escapeHtml(err.message)}</p>`;
      metaEl.textContent = "";
    } finally {
      setLoading(false);
    }
  }

  async function openPatient(mrn, fallbackName) {
    if (!mrn) return;
    drawerTitle.textContent = fallbackName || "Loading…";
    drawerMrn.textContent = mrn;
    drawerSummary.textContent = "Loading clinical summary…";
    drawerDisclaimer.textContent = "";
    drawerResources.innerHTML = `<li class="text-sm text-ink/60">Loading resources…</li>`;
    showDrawer();

    try {
      const res = await fetch(`/api/patients/${encodeURIComponent(mrn)}`);
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Patient lookup failed (${res.status}): ${detail}`);
      }
      const data = await res.json();
      const summaryObj = data.summary && typeof data.summary === "object" ? data.summary : null;
      drawerTitle.textContent = data.patient_name || fallbackName || "Patient";
      drawerMrn.textContent = data.mrn || mrn;
      drawerSummary.textContent =
        (summaryObj && summaryObj.summary) ||
        (typeof data.summary === "string" ? data.summary : null) ||
        "No summary available.";
      drawerDisclaimer.textContent = (summaryObj && summaryObj.disclaimer) || "";
      drawerResources.innerHTML = (data.resources || [])
        .filter((r) => r.resourceType && r.resourceType !== "Patient")
        .map(
          (r) =>
            `<li class="border border-ink/10 px-3 py-2"><span class="text-xs font-semibold uppercase tracking-wide text-tide">${escapeHtml(r.resourceType)}</span><p class="text-sm">${escapeHtml(r.display || r.id || "")}</p></li>`
        )
        .join("") || `<li class="text-sm text-ink/60">No linked media resources.</li>`;
    } catch (err) {
      drawerSummary.textContent = err.message || "Failed to load patient detail.";
      drawerResources.innerHTML = "";
    }
  }

  let hideTimer = null;

  function showDrawer() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    drawer.hidden = false;
    drawer.removeAttribute("hidden");
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => drawer.classList.add("drawer-open"));
    drawerClose.focus();
  }

  function hideDrawer() {
    drawer.classList.remove("drawer-open");
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      drawer.hidden = true;
      drawer.setAttribute("hidden", "");
      hideTimer = null;
    }, 220);
    queryInput.focus();
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  form.addEventListener("submit", doSearch);
  drawerClose.addEventListener("click", hideDrawer);
  overlay.addEventListener("click", hideDrawer);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !drawer.hidden) {
      hideDrawer();
      return;
    }
    if (document.activeElement === resultsEl || resultsEl.contains(document.activeElement)) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, currentResults.length - 1);
        highlightActive();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        highlightActive();
      } else if (e.key === "Enter" && activeIndex >= 0) {
        const hit = currentResults[activeIndex];
        openPatient(hit.mrn, hit.patient_name);
      }
    }
  });

  resultsEl.addEventListener("focus", () => {
    if (activeIndex < 0 && currentResults.length) activeIndex = 0;
    highlightActive();
  });

  fetch("/api/health")
    .then((r) => r.json())
    .then((h) => {
      healthPill.textContent = h.ready
        ? `${h.bundle_count} patients indexed`
        : `Degraded: ${h.startup_error || "not ready"}`;
    })
    .catch(() => {
      healthPill.textContent = "API unreachable";
    });
})();
