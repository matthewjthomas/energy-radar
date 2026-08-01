// Shared Chart.js builders for the usage/weather combo chart and forecast chart.
const _chartInstances = {};
const _lastUsageWeatherArgs = {};
const _lastForecastArgs = {};

function _cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function _chartColors() {
  return {
    text: _cssVar("--chart-text", "#1e1e1c"),
    textMuted: _cssVar("--chart-text-muted", "#3d3d38"),
    grid: _cssVar("--chart-grid", "#d8d8d4"),
    tempLine: _cssVar("--chart-temp-line", "#2a7080"),
    tooltipBg: _cssVar("--chart-tooltip-bg", "#1e1e1c"),
    tooltipText: _cssVar("--chart-tooltip-text", "#fdfdfb"),
  };
}

function _sourceColor(source) {
  const vars = { electricity: "--electricity", gas: "--gas", water: "--water" };
  const fromCss = vars[source] ? _cssVar(vars[source], "") : "";
  return fromCss || SOURCE_COLORS[source] || "#888888";
}

function _hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function _solidLegendTooltipPlugins(colors) {
  return {
    legend: {
      labels: {
        color: colors.text,
        usePointStyle: true,
        pointStyle: "rect",
        boxWidth: 12,
        boxHeight: 12,
      },
    },
    tooltip: {
      usePointStyle: true,
      boxPadding: 6,
      backgroundColor: colors.tooltipBg,
      titleColor: colors.tooltipText,
      bodyColor: colors.tooltipText,
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

  const colors = _chartColors();
  const tempByDay = _dailyAvgTemp(weatherPoints || []);
  const allDays = new Set(Object.keys(tempByDay));
  for (const points of Object.values(usageBySource)) {
    for (const p of points) allDays.add(p.date);
  }
  const labels = [...allDays].sort();
  const today = new Date().toISOString().slice(0, 10);

  const datasets = [];
  for (const [source, points] of Object.entries(usageBySource)) {
    const byDate = Object.fromEntries(points.map((p) => [p.date, p.value]));
    const unitSuffix = units[source] ? ` (${units[source]})` : "";
    datasets.push({
      type: "bar",
      label: `${SOURCE_LABELS[source] || source}${unitSuffix}`,
      data: labels.map((d) => byDate[d] ?? null),
      backgroundColor: _sourceColor(source),
      yAxisID: "y",
      borderRadius: 3,
    });
  }
  datasets.push({
    type: "line",
    label: `Avg temp (${tempUnitLabel()})`,
    data: labels.map((d) => (tempByDay[d] !== undefined ? convertTemp(tempByDay[d]) : null)),
    borderColor: colors.tempLine,
    backgroundColor: "transparent",
    yAxisID: "y1",
    tension: 0.3,
    pointRadius: 2,
    ..._lineDatasetPointStyle(colors.tempLine),
  });

  _chartInstances[canvasId] = new Chart(canvas, {
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: {
          position: "left",
          title: { display: true, text: "Usage", color: colors.textMuted },
          ticks: { color: colors.textMuted },
          grid: { color: colors.grid },
        },
        y1: {
          position: "right",
          title: { display: true, text: tempUnitLabel(), color: colors.textMuted },
          ticks: { color: colors.textMuted },
          grid: { drawOnChartArea: false, color: colors.grid },
        },
        x: {
          ticks: {
            color: (ctx) => (labels[ctx.index] === today ? colors.text : colors.textMuted),
          },
          grid: { color: colors.grid },
        },
      },
      plugins: {
        ..._solidLegendTooltipPlugins(colors),
      },
    },
  });
}

function renderForecastChart(canvasId, forecastPoints, source, unit = "") {
  _lastForecastArgs[canvasId] = { forecastPoints, source, unit };
  _destroyChart(canvasId);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const colors = _chartColors();
  const unitSuffix = unit ? ` (${unit})` : "";
  const labels = forecastPoints.map((p) => p.date);
  const hasTemps = forecastPoints.some((p) => p.high_temp_c != null);
  const usageColor = _sourceColor(source);
  const tempColor = colors.tempLine;
  const today = new Date().toISOString().slice(0, 10);

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
          data: forecastPoints.map((p) => (p.high_temp_c != null ? convertTemp(p.high_temp_c) : null)),
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
          ticks: {
            color: (ctx) => (labels[ctx.index] === today ? colors.text : colors.textMuted),
          },
          grid: { color: colors.grid },
        },
        y: {
          ticks: { color: colors.textMuted },
          title: { display: true, text: "Usage", color: colors.textMuted },
          grid: { color: colors.grid },
        },
        ...(hasTemps ? {
          y1: {
            position: "right",
            title: { display: true, text: tempUnitLabel(), color: colors.textMuted },
            ticks: { color: colors.textMuted },
            grid: { drawOnChartArea: false, color: colors.grid },
          },
        } : {}),
      },
      plugins: {
        ..._solidLegendTooltipPlugins(colors),
      },
    },
  });
}

function _rerenderAllCharts() {
  for (const [canvasId, args] of Object.entries(_lastUsageWeatherArgs)) {
    renderUsageWeatherChart(canvasId, args.usageBySource, args.weatherPoints, args.eventMarkers, args.units);
  }
  for (const [canvasId, args] of Object.entries(_lastForecastArgs)) {
    renderForecastChart(canvasId, args.forecastPoints, args.source, args.unit);
  }
}

document.addEventListener("tempunitchange", _rerenderAllCharts);
document.addEventListener("themechange", _rerenderAllCharts);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (document.documentElement.getAttribute("data-theme") === "system") {
    _rerenderAllCharts();
  }
});
