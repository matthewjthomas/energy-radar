// Dashboard page logic.
let currentRange = defaultRange();

function defaultRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 6);
  return { start: toISODate(start), end: toISODate(end) };
}

function toISODate(d) {
  return d.toISOString().slice(0, 10);
}

function rangeQuery() {
  return `start=${currentRange.start}&end=${currentRange.end}`;
}

async function loadSummaryCards(usageBySource) {
  const container = document.getElementById("summary-cards");
  container.innerHTML = "";
  const sources = Object.keys(usageBySource);
  if (sources.length === 0) {
    container.innerHTML = `<div class="card"><div class="card-label">No sources enabled</div>
      <div class="card-sub">Map Home Assistant entities in <a href="/settings">Settings</a>.</div></div>`;
    return;
  }
  for (const source of sources) {
    const points = usageBySource[source];
    const total = points.reduce((a, p) => a + p.value, 0);
    const cost = points.reduce((a, p) => a + (p.cost || 0), 0);
    const hasCost = points.some((p) => p.cost !== null && p.cost !== undefined);
    const card = document.createElement("div");
    card.className = `card accent-${source}`;
    card.innerHTML = `
      <div class="card-label">${SOURCE_LABELS[source] || source}</div>
      <div class="card-value">${fmtNumber(total)}</div>
      <div class="card-sub">${hasCost ? "$" + fmtNumber(cost, 2) + " estimated" : "total this period"}</div>
    `;
    container.appendChild(card);
  }
}

async function loadInsights(sources) {
  const list = document.getElementById("insights-list");
  list.innerHTML = "";
  if (sources.length === 0) {
    list.innerHTML = `<div class="insight-item">Map a Home Assistant source in Settings to see insights.</div>`;
    return;
  }
  for (const source of sources) {
    try {
      const corr = await Api.get(`/api/correlation?source=${source}`);
      const item = document.createElement("div");
      item.className = "insight-item";
      const direction = corr.hdd_coef > corr.cdd_coef ? "colder days" : "warmer days";
      item.innerHTML = `<span>${SOURCE_LABELS[source]} tracks weather with R\u00b2=${fmtNumber(corr.r_squared, 2)} (mostly driven by ${direction})</span>`;
      list.appendChild(item);
    } catch (e) {
      // Not enough data yet; skip silently.
    }
  }
}

async function loadForecast(sources) {
  if (sources.length === 0) return;
  const source = sources[0];
  try {
    const forecast = await Api.get(`/api/forecast/usage?source=${source}&days=14`);
    renderForecastChart("forecast-chart", forecast, source);
  } catch (e) {
    console.warn("Forecast unavailable", e);
  }
}

async function refreshDashboard() {
  const [usageBySource, weather] = await Promise.all([
    Api.get(`/api/usage?${rangeQuery()}`),
    Api.get(`/api/weather?${rangeQuery()}`),
  ]);
  const sources = Object.keys(usageBySource);
  renderUsageWeatherChart("usage-weather-chart", usageBySource, weather);
  await loadSummaryCards(usageBySource);
  await loadInsights(sources);
  await loadForecast(sources);
}

document.addEventListener("DOMContentLoaded", () => {
  flatpickr("#range-input", {
    mode: "range",
    dateFormat: "Y-m-d",
    defaultDate: [currentRange.start, currentRange.end],
    onClose: (selectedDates) => {
      if (selectedDates.length === 2) {
        currentRange = { start: toISODate(selectedDates[0]), end: toISODate(selectedDates[1]) };
        refreshDashboard();
      }
    },
  });
  refreshDashboard();
});
