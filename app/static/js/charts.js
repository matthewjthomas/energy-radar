// Shared Chart.js builders for the usage/weather combo chart and forecast chart.
const _chartInstances = {};
const _lastUsageWeatherArgs = {};

function _hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function _solidLegendTooltipPlugins() {
  return {
    legend: {
      labels: {
        color: "#e7ecf7",
        usePointStyle: true,
        pointStyle: "rect",
        boxWidth: 12,
        boxHeight: 12,
      },
    },
    tooltip: {
      usePointStyle: true,
      boxPadding: 6,
      callbacks: {
        labelColor: (item) => ({
          borderColor: "transparent",
          backgroundColor: item.dataset.borderColor,
          borderWidth: 0,
          borderRadius: 2,
        }),
      },
    },
  };
}

function _lineDatasetPointStyle(color) {
  return {
    pointStyle: "rect",
    pointBackgroundColor: color,
    pointBorderColor: color,
    pointBorderWidth: 0,
  };
}

function _destroyChart(canvasId) {
  if (_chartInstances[canvasId]) {
    _chartInstances[canvasId].destroy();
    delete _chartInstances[canvasId];
  }
}

function _dailyAvgTemp(weatherPoints) {
  const byDay = {};
  for (const p of weatherPoints) {
    if (p.temperature_c === null || p.temperature_c === undefined) continue;
    const day = p.time.slice(0, 10);
    (byDay[day] ||= []).push(p.temperature_c);
  }
  const out = {};
  for (const [day, vals] of Object.entries(byDay)) {
    out[day] = vals.reduce((a, b) => a + b, 0) / vals.length;
  }
  return out;
}

function renderUsageWeatherChart(canvasId, usageBySource, weatherPoints, eventMarkers = [], units = {}) {
  _lastUsageWeatherArgs[canvasId] = { usageBySource, weatherPoints, eventMarkers, units };
  _destroyChart(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const tempByDay = _dailyAvgTemp(weatherPoints || []);
  const allDays = new Set(Object.keys(tempByDay));
  for (const points of Object.values(usageBySource)) {
    for (const p of points) allDays.add(p.date);
  }
  const labels = [...allDays].sort();

  const datasets = [];
  for (const [source, points] of Object.entries(usageBySource)) {
    const byDate = Object.fromEntries(points.map((p) => [p.date, p.value]));
    const unitSuffix = units[source] ? ` (${units[source]})` : "";
    datasets.push({
      type: "bar",
      label: `${SOURCE_LABELS[source] || source}${unitSuffix}`,
      data: labels.map((d) => byDate[d] ?? null),
      backgroundColor: SOURCE_COLORS[source] || "#888",
      yAxisID: "y",
      borderRadius: 4,
    });
  }
  datasets.push({
    type: "line",
    label: `Avg temp (${tempUnitLabel()})`,
    data: labels.map((d) => (tempByDay[d] !== undefined ? convertTemp(tempByDay[d]) : null)),
    borderColor: "#e7ecf7",
    backgroundColor: "transparent",
    yAxisID: "y1",
    tension: 0.3,
    pointRadius: 2,
    ..._lineDatasetPointStyle("#e7ecf7"),
  });

  const eventLines = (eventMarkers || []).map((e) => ({
    type: "line",
    xMin: e.event_date,
    xMax: e.event_date,
    label: e.title,
  }));

  _chartInstances[canvasId] = new Chart(canvas, {
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { position: "left", title: { display: true, text: "Usage" }, grid: { color: "#2a3550" } },
        y1: {
          position: "right",
          title: { display: true, text: tempUnitLabel() },
          grid: { drawOnChartArea: false },
        },
        x: {
          grid: { color: "#2a3550" },
          ticks: {
            color: (ctx) => {
              const today = new Date().toISOString().slice(0, 10);
              return labels[ctx.index] === today ? "#e7ecf7" : "#8b96ad";
            },
          },
        },
      },
      plugins: {
        ..._solidLegendTooltipPlugins(),
      },
    },
  });
}

function renderForecastChart(canvasId, forecastPoints, source, unit = "") {
  _destroyChart(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const unitSuffix = unit ? ` (${unit})` : "";
  const labels = forecastPoints.map((p) => p.date);
  const hasTemps = forecastPoints.some((p) => p.high_temp_c != null);
  const usageColor = SOURCE_COLORS[source] || "#4fd1c5";
  const tempColor = "#e7ecf7";
  _chartInstances[canvasId] = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `Predicted ${SOURCE_LABELS[source] || source} usage${unitSuffix}`,
          data: forecastPoints.map((p) => p.predicted_value),
          borderColor: usageColor,
          backgroundColor: _hexToRgba(usageColor, 0.15),
          fill: true,
          tension: 0.3,
          yAxisID: "y",
          ..._lineDatasetPointStyle(usageColor),
        },
        ...(hasTemps ? [{
          label: `High temp (${tempUnitLabel()})`,
          data: forecastPoints.map((p) => p.high_temp_c != null ? convertTemp(p.high_temp_c) : null),
          borderColor: tempColor,
          backgroundColor: "transparent",
          borderDash: [4, 3],
          tension: 0.3,
          pointRadius: 2,
          yAxisID: "y1",
          ..._lineDatasetPointStyle(tempColor),
        }] : []),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { color: "#2a3550" },
          ticks: {
            color: (ctx) => {
              const today = new Date().toISOString().slice(0, 10);
              return labels[ctx.index] === today ? "#e7ecf7" : "#8b96ad";
            },
          },
        },
        y: { grid: { color: "#2a3550" } },
        ...(hasTemps ? { y1: {
          position: "right",
          title: { display: true, text: tempUnitLabel() },
          grid: { drawOnChartArea: false },
        }} : {}),
      },
      plugins: {
        ..._solidLegendTooltipPlugins(),
      },
    },
  });
}

// Re-render any already-drawn usage/weather charts (with their last data) when
// the user toggles between Fahrenheit and Celsius, without refetching from the API.
document.addEventListener("tempunitchange", () => {
  for (const [canvasId, args] of Object.entries(_lastUsageWeatherArgs)) {
    renderUsageWeatherChart(canvasId, args.usageBySource, args.weatherPoints, args.eventMarkers, args.units);
  }
});

