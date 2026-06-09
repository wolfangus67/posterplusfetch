const state = {
  config: null,
  loadedCatalogs: [],
  selectedCatalogs: [],
  runtime: null,
  polling: null,
};

const $ = (selector) => document.querySelector(selector);

const toast = $("#toast");
const togglePrefetch = $("#togglePrefetch");
const activePill = $("#activePill");
const manifestStatus = $("#manifestStatus");
const catalogCount = $("#catalogCount");
const catalogList = $("#catalogList");
const runStatus = $("#runStatus");
const lastRun = $("#lastRun");
const nextRun = $("#nextRun");
const processed = $("#processed");
const newCache = $("#newCache");
const errors = $("#errors");
const daysContainer = $("#days");
const saveBtn = $("#saveBtn");
const runNowBtn = $("#runNow");
const loadCatalogsBtn = $("#loadCatalogs");
const catalogUrlInput = $("#catalogUrl");
const aioUrlInput = $("#aioUrl");
const runTimeInput = $("#runTime");
const limitInput = $("#limit");
const includeMoviesInput = $("#includeMovies");
const includeSeriesInput = $("#includeSeries");
const refreshExpiredInput = $("#refreshExpired");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.remove("show"), 2200);
}

function formatDateTime(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function renderDays(days) {
  daysContainer.querySelectorAll(".chip").forEach((chip) => {
    const day = Number(chip.dataset.day);
    chip.classList.toggle("off", !days.includes(day));
  });
}

function readDays() {
  return Array.from(daysContainer.querySelectorAll(".chip"))
    .filter((chip) => !chip.classList.contains("off"))
    .map((chip) => Number(chip.dataset.day));
}

function getSelectedCatalogs() {
  return state.loadedCatalogs.filter((catalog) => catalog.selected !== false);
}

function renderCatalogs(catalogs) {
  catalogList.innerHTML = catalogs
    .map((catalog) => {
      const extraTags = Array.isArray(catalog.extra) ? catalog.extra.length : 0;
      return `
        <article class="catalog-item ${catalog.selected === false ? "disabled" : ""}" data-key="${catalog.key}">
          <div class="catalog-body">
            <strong>${catalog.name}</strong>
            <span>${catalog.type} · ${catalog.pageSize} items par page · ${catalog.source}</span>
            <small>${extraTags ? `${extraTags} champs extra` : "Pas de filtres supplémentaires"}</small>
          </div>
          <label class="catalog-switch">
            <input type="checkbox" ${catalog.selected === false ? "" : "checked"} data-action="toggle-catalog" />
            <span></span>
          </label>
        </article>
      `;
    })
    .join("");

  catalogList.querySelectorAll('[data-action="toggle-catalog"]').forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      const row = event.target.closest(".catalog-item");
      const key = row.dataset.key;
      const catalog = state.loadedCatalogs.find((item) => item.key === key);
      if (catalog) {
        catalog.selected = event.target.checked;
        row.classList.toggle("disabled", !event.target.checked);
        state.selectedCatalogs = getSelectedCatalogs();
        catalogCount.textContent = String(state.selectedCatalogs.length);
      }
    });
  });

  catalogCount.textContent = String(getSelectedCatalogs().length);
}

function renderStatus(payload) {
  state.config = payload.config;
  state.loadedCatalogs = payload.catalogs.loaded || [];
  state.selectedCatalogs = payload.catalogs.selected || [];
  state.runtime = payload.runtime;

  const enabled = Boolean(payload.config.schedule_enabled);
  togglePrefetch.classList.toggle("on", enabled);
  togglePrefetch.setAttribute("aria-pressed", String(enabled));
  activePill.textContent = enabled ? "Actif" : "Désactivé";
  activePill.style.background = enabled ? "rgba(46, 174, 86, 0.22)" : "rgba(122, 122, 122, 0.22)";
  activePill.style.color = enabled ? "#8df6a8" : "#d0d3de";

  catalogUrlInput.value = payload.config.catalog_manifest_url || "";
  aioUrlInput.value = payload.config.stream_manifest_url || "";
  runTimeInput.value = payload.config.run_time || "03:00";
  limitInput.value = payload.config.limit || 500;
  includeMoviesInput.checked = Boolean(payload.config.include_movies);
  includeSeriesInput.checked = Boolean(payload.config.include_series);
  refreshExpiredInput.checked = Boolean(payload.config.refresh_expired);
  renderDays(payload.config.days || [0, 1, 2, 3, 4, 5, 6]);

  renderCatalogs(state.loadedCatalogs);

  manifestStatus.textContent = payload.runtime.message || "En attente";
  runStatus.textContent = payload.runtime.state === "running"
    ? "En cours de traitement"
    : payload.runtime.message || "En attente";
  lastRun.textContent = payload.runtime.last_run ? formatDateTime(payload.runtime.last_run) : "Aucune exécution";
  nextRun.textContent = payload.runtime.next_run ? formatDateTime(payload.runtime.next_run) : "Non planifié";
  processed.textContent = `${payload.runtime.processed || 0}`;
  newCache.textContent = `${payload.runtime.cached || 0}`;
  errors.textContent = `${payload.runtime.errors || 0}`;

  const total = payload.runtime.total || 1;
  const progress = payload.runtime.progress || 0;
  if (payload.runtime.state === "running") {
    manifestStatus.textContent = `Préchargement en cours · ${((Math.min(progress, total) / total) * 100).toFixed(0)}%`;
  }
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    throw new Error(payload.error || `Erreur HTTP ${response.status}`);
  }
  return payload;
}

async function refreshStatus() {
  const payload = await api("/api/status");
  renderStatus(payload);
}

async function loadCatalogs() {
  const payload = await api("/api/catalogs/load", {
    method: "POST",
    body: JSON.stringify({
      catalog_manifest_url: catalogUrlInput.value.trim(),
      stream_manifest_url: aioUrlInput.value.trim(),
    }),
  });

  showToast("Catalogues chargés");
  await refreshStatus();
  manifestStatus.textContent = `Manifest chargé · ${payload.addon.catalog_count} catalogues trouvés`;
}

async function validateManifest(url, label) {
  const payload = await api("/api/addon/manifest", {
    method: "POST",
    body: JSON.stringify({ url }),
  });

  showToast(`${label} validé`);
  manifestStatus.textContent = `${label} · ${payload.addon.name} · ${payload.addon.catalog_count} catalogues`;
}

async function saveSchedule() {
  const payload = await api("/api/schedule", {
    method: "POST",
    body: JSON.stringify({
      enabled: togglePrefetch.classList.contains("on"),
      run_time: runTimeInput.value,
      days: readDays(),
      limit: Number(limitInput.value || 500),
      include_movies: includeMoviesInput.checked,
      include_series: includeSeriesInput.checked,
      refresh_expired: refreshExpiredInput.checked,
    }),
  });

  state.config = payload.schedule;
  showToast("Réglages enregistrés");
  await refreshStatus();
}

async function saveCatalogSelection() {
  await api("/api/catalogs/selection", {
    method: "POST",
    body: JSON.stringify({
      catalogs: state.loadedCatalogs,
    }),
  });
}

async function runNow() {
  await api("/api/run", { method: "POST", body: JSON.stringify({}) });
  showToast("Préfetch lancé");
  await refreshStatus();
}

function setupEvents() {
  togglePrefetch.addEventListener("click", async () => {
    const enabled = !togglePrefetch.classList.contains("on");
    togglePrefetch.classList.toggle("on", enabled);
    togglePrefetch.setAttribute("aria-pressed", String(enabled));
    activePill.textContent = enabled ? "Actif" : "Désactivé";
    showToast(enabled ? "Préfetch activé" : "Préfetch désactivé");
    try {
      await saveSchedule();
    } catch (error) {
      showToast(error.message);
    }
  });

  daysContainer.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.classList.toggle("off");
    });
  });

  loadCatalogsBtn.addEventListener("click", async () => {
    loadCatalogsBtn.disabled = true;
    try {
      await loadCatalogs();
    } catch (error) {
      showToast(error.message);
    } finally {
      loadCatalogsBtn.disabled = false;
    }
  });

  document.querySelector('[data-test="catalog"]').addEventListener("click", async () => {
    try {
      await validateManifest(catalogUrlInput.value.trim(), "Manifest catalogue");
    } catch (error) {
      showToast(error.message);
    }
  });

  document.querySelector('[data-test="aio"]').addEventListener("click", async () => {
    try {
      await validateManifest(aioUrlInput.value.trim(), "Manifest flux");
    } catch (error) {
      showToast(error.message);
    }
  });

  saveBtn.addEventListener("click", async () => {
    try {
      await saveCatalogSelection();
      await saveSchedule();
    } catch (error) {
      showToast(error.message);
    }
  });

  runNowBtn.addEventListener("click", async () => {
    runNowBtn.disabled = true;
    try {
      await runNow();
    } catch (error) {
      showToast(error.message);
    } finally {
      runNowBtn.disabled = false;
    }
  });
}

async function boot() {
  setupEvents();
  await refreshStatus();
  clearInterval(state.polling);
  state.polling = setInterval(() => {
    refreshStatus().catch((error) => console.warn(error));
  }, 5000);
}

boot().catch((error) => {
  console.error(error);
  showToast(error.message);
});
