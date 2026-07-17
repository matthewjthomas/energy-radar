// Shared Chart.js builders for the usage/weather combo chart and forecast chart.
const _chartInstances = {};

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

function renderUsageWeatherChart(canvasId, usageBySource, weatherPoints, eventMarkers = []) {
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
    datasets.push({
      type: "bar",
      label: SOURCE_LABELS[source] || source,
      data: labels.map((d) => byDate[d] ?? null),
      backgroundColor: SOURCE_COLORS[source] || "#888",
      yAxisID: "y",
      borderRadius: 4,
    });
  }
  datasets.push({
    type: "line",
    label: "Avg temp (\u00b0C)",
    data: labels.map((d) => tempByDay[d] ?? null),
    borderColor: "#e7ecf7",
    backgroundColor: "transparent",
    yAxisID: "y1",
    tension: 0.3,
    pointRadius: 2,
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
          title: { display: true, text: "\u00b0C" },
          grid: { drawOnChartArea: false },
        },
        x: { grid: { color: "#2a3550" } },
      },
      plugins: {
        legend: { labels: { color: "#e7ecf7" } },
      },
    },
  });
}

function renderForecastChart(canvasId, forecastPoints, source) {
  _destroyChart(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const labels = forecastPoints.map((p) => p.date);
  _chartInstances[canvasId] = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `Predicted ${SOURCE_LABELS[source] || source} usage`,
          data: forecastPoints.map((p) => p.predicted_value),
          borderColor: SOURCE_COLORS[source] || "#4fd1c5",
          backgroundColor: "rgba(79, 209, 197, 0.15)",
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { grid: { color: "#2a3550" } },
        y: { grid: { color: "#2a3550" } },
      },
      plugins: { legend: { labels: { color: "#e7ecf7" } } },
    },
  });
}
