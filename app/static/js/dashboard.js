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

function formatPeriodLabel(startISO, endISO) {
  const start = new Date(`${startISO}T12:00:00`);
  const end = new Date(`${endISO}T12:00:00`);
  const full = { month: "long", day: "numeric", year: "numeric" };
  if (startISO === endISO) return start.toLocaleDateString(undefined, full);
  const sameYear = start.getFullYear() === end.getFullYear();
  const startFmt = start.toLocaleDateString(
    undefined,
    sameYear ? { month: "long", day: "numeric" } : full
  );
  return `${startFmt} – ${end.toLocaleDateString(undefined, full)}`;
}

function proseHighlight(source, text) {
  return `<span class="prose-highlight ${source}">${text}</span>`;
}

function loadProseSummary(usageBySource, weather, units) {
  const period = document.getElementById("prose-period");
  const summary = document.getElementById("prose-summary");
  if (!period || !summary) return;

  period.textContent = formatPeriodLabel(currentRange.start, currentRange.end);

  const sources = Object.keys(usageBySource);
  if (sources.length === 0) {
    summary.innerHTML = `No utility sources are mapped yet. Head to <a href="${withBase("/settings")}">Settings</a> to connect Home Assistant sensors.`;
    return;
  }

  const parts = [];
  for (const source of sources) {
    const points = usageBySource[source];
    if (!points.length) continue;
    const avg = points.reduce((a, p) => a + p.value, 0) / points.length;
    const unit = units[source] ? ` ${units[source]}` : "";
    const label = SOURCE_LABELS[source] || source;
    parts.push(
      `${label} averaged ${proseHighlight(source, `${fmtNumber(avg)}${unit}`)} per day`
    );
  }

  let maxTempC = null;
  for (const p of weather || []) {
    if (p.temperature_c != null) {
      maxTempC = maxTempC == null ? p.temperature_c : Math.max(maxTempC, p.temperature_c);
    }
  }

  let text = parts.join(". ");
  if (text) text += ".";
  if (maxTempC != null) {
    const warmest = `${fmtNumber(convertTemp(maxTempC), 0)}${tempUnitLabel()}`;
    text += ` Temperatures reached ${proseHighlight("temperature", warmest)} on the warmest day in this period.`;
  }
  if (!text) {
    text = "No usage recorded for this date range yet.";
  }
  summary.innerHTML = text;
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
      item.innerHTML = `${SOURCE_LABELS[source]} tracks weather with R\u00b2=${fmtNumber(corr.r_squared, 2)} (mostly driven by ${direction}).`;
      list.appendChild(item);
    } catch (e) {
      // Not enough overlapping usage/weather history yet for this source.
    }
  }
  if (!any) {
    list.innerHTML = `<div class="insight-item">Still collecting historical data &mdash; insights need about 3 full days of overlapping usage and weather.</div>`;
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
    status.textContent = "Predicted usage for the next 14 days, based on forecast weather.";
    renderForecastChart("forecast-chart", forecast, source, units[source]);
  } catch (e) {
    status.textContent = "Still collecting historical data \u2014 forecasts need about 3 full days of overlapping usage and weather.";
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
  loadProseSummary(usageBySource, weather, units);
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
