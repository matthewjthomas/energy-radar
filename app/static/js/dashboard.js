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

async function loadSummaryCards(usageBySource, units) {
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
    const unit = units[source] ? ` ${units[source]}` : "";
    const card = document.createElement("div");
    card.className = `card accent-${source}`;
    card.innerHTML = `
      <div class="card-label">${SOURCE_LABELS[source] || source}</div>
      <div class="card-value">${fmtNumber(total)}${unit}</div>
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
  let any = false;
  for (const source of sources) {
    try {
      const corr = await Api.get(`/api/correlation?source=${source}`);
      any = true;
      const item = document.createElement("div");
      item.className = "insight-item";
      const direction = corr.hdd_coef > corr.cdd_coef ? "colder days" : "warmer days";
      item.innerHTML = `<span>${SOURCE_LABELS[source]} tracks weather with R\u00b2=${fmtNumber(corr.r_squared, 2)} (mostly driven by ${direction})</span>`;
      list.appendChild(item);
    } catch (e) {
      // Not enough overlapping usage/weather history yet for this source.
    }
  }
  if (!any) {
    list.innerHTML = `<div class="insight-item">Still collecting historical data &mdash; insights appear once a few days of usage and weather overlap.</div>`;
  }
}

async function loadForecast(sources, units) {
  const status = document.getElementById("forecast-status");
  if (sources.length === 0) {
    status.textContent = "";
    return;
  }
  const source = sources[0];
  try {
    const forecast = await Api.get(`/api/forecast/usage?source=${source}&days=14`);
    status.textContent = "";
    renderForecastChart("forecast-chart", forecast, source, units[source]);
  } catch (e) {
    status.textContent = "Still collecting historical data \u2014 forecasts appear once there's enough usage/weather history to correlate.";
    console.warn("Forecast unavailable", e);
  }
}

async function refreshDashboard() {
  const [usageBySource, weather, units] = await Promise.all([
    Api.get(`/api/usage?${rangeQuery()}`),
    Api.get(`/api/weather?${rangeQuery()}`),
    Api.get("/api/sources/units"),
  ]);
  const sources = Object.keys(usageBySource);
  renderUsageWeatherChart("usage-weather-chart", usageBySource, weather, [], units);
  await loadSummaryCards(usageBySource, units);
  await loadInsights(sources);
  await loadForecast(sources, units);
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
