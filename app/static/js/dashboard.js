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

const HEATING_FUEL_LABELS = {
  electric: "electric heating",
  gas: "gas heating",
  heat_pump: "heat pump",
  dual: "dual-fuel heating",
  unknown: "unknown heating",
};

function forecastableSources(usageBySource, hvac) {
  const sources = new Set(Object.keys(usageBySource));
  if (!hvac || !hvac.enabled) return [...sources];
  const heat = hvac.heating_fuel;
  if (heat === "electric" || heat === "heat_pump" || heat === "dual" || heat === "unknown") {
    sources.add("electricity");
  }
  if (heat === "gas" || heat === "dual" || heat === "unknown") {
    sources.add("gas");
  }
  if (hvac.cooling_fuel === "electric" || hvac.cooling_fuel === "unknown") {
    sources.add("electricity");
  }
  return [...sources];
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

async function loadInsights(sources, hvac) {
  const list = document.getElementById("insights-list");
  list.innerHTML = "";
  const forecastSources = forecastableSources(Object.fromEntries(sources.map((s) => [s, true])), hvac);
  if (forecastSources.length === 0) {
    list.innerHTML = `<div class="insight-item">Map a utility meter or thermostat in Settings to see insights.</div>`;
    return;
  }
  let any = false;
  for (const source of forecastSources) {
    try {
      const corr = await Api.get(`/api/correlation?source=${source}`);
      any = true;
      const item = document.createElement("div");
      item.className = "insight-item";
      const direction = corr.hdd_coef > corr.cdd_coef ? "colder days" : "warmer days";
      const estimated = corr.is_estimated
        ? " (estimated from thermostat setpoint and runtime)"
        : "";
      const thermostatNote =
        corr.setpoint_coef && Math.abs(corr.setpoint_coef) > 0.01
          ? " Setpoint also influences the model."
          : "";
      item.innerHTML = `${SOURCE_LABELS[source] || source} tracks weather with R\u00b2=${fmtNumber(corr.r_squared, 2)} (mostly driven by ${direction})${estimated}.${thermostatNote}`;
      list.appendChild(item);
    } catch (e) {
      // Not enough overlapping history yet for this source.
    }
  }
  if (!any) {
    list.innerHTML = `<div class="insight-item">Still collecting data &mdash; insights need about 3 days of overlapping thermostat/weather or meter history.</div>`;
  }
  if (hvac && hvac.enabled) {
    const fuel = HEATING_FUEL_LABELS[hvac.heating_fuel] || hvac.heating_fuel;
    const item = document.createElement("div");
    item.className = "insight-item muted";
    item.textContent = `HVAC configured: ${fuel}, cooling via ${hvac.cooling_fuel}.`;
    list.appendChild(item);
  }
}

async function loadForecast(sources, units, hvac) {
  const status = document.getElementById("forecast-status");
  const forecastSources = forecastableSources(Object.fromEntries(sources.map((s) => [s, true])), hvac);
  if (forecastSources.length === 0) {
    status.textContent = "";
    return;
  }
  const source = forecastSources.includes("electricity")
    ? "electricity"
    : forecastSources[0];
  try {
    const [forecast, biasRows] = await Promise.all([
      Api.get(`/api/forecast/usage?source=${source}&days=14`),
      Api.get("/api/forecast/bias").catch(() => []),
    ]);
    const estimated = forecast.some((p) => p.is_estimated);
    const bias = (biasRows || []).find((row) => row.source_type === source);
    let statusText = estimated
      ? `Estimated ${SOURCE_LABELS[source] || source} for the next 14 days using thermostat setpoint, runtime, and forecast weather (no meter data).`
      : "Predicted usage for the next 14 days, based on forecast weather and thermostat history when available.";
    if (bias && bias.scored_samples >= 3 && bias.mape_30d != null) {
      statusText += ` Calibrated from ${bias.scored_samples} scored day${bias.scored_samples === 1 ? "" : "s"} (30-day MAPE ${fmtNumber(bias.mape_30d, 1)}%).`;
    } else if (bias && bias.scored_samples > 0) {
      statusText += " Forecast calibration is learning from recent actuals.";
    }
    status.textContent = statusText;
    renderForecastChart("forecast-chart", forecast, source, units[source]);
  } catch (e) {
    status.textContent = "Still collecting data \u2014 forecasts need meter history or a mapped thermostat with a few days of readings.";
    console.warn("Forecast unavailable", e);
  }
}

async function refreshDashboard() {
  const [usageBySource, weather, units, hvac] = await Promise.all([
    Api.get(`/api/usage?${rangeQuery()}`),
    Api.get(`/api/weather?${rangeQuery()}`),
    Api.get("/api/sources/units"),
    Api.get("/api/hvac").catch(() => null),
  ]);
  const sources = Object.keys(usageBySource);
  renderUsageWeatherChart("usage-weather-chart", usageBySource, weather, [], units);
  loadProseSummary(usageBySource, weather, units);
  await loadInsights(sources, hvac);
  await loadForecast(sources, units, hvac);
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
