// History page logic: free date-range browsing, trend shifts, and event markers.
let currentRange = defaultRange();
let cachedEvents = [];

function defaultRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 30);
  return { start: toISODate(start), end: toISODate(end) };
}

function toISODate(d) {
  return d.toISOString().slice(0, 10);
}

function rangeQuery() {
  return `start=${currentRange.start}&end=${currentRange.end}`;
}

function formatMonthLabel(year, month) {
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, {
    month: "short",
    year: "numeric",
  });
}

async function loadMonthlySummaries(units) {
  const container = document.getElementById("monthly-summary");
  let summaries;
  try {
    summaries = await Api.get("/api/usage/monthly");
  } catch (e) {
    container.innerHTML = `<p class="muted">Unable to load monthly summaries.</p>`;
    return;
  }

  if (!summaries.length) {
    container.innerHTML = `<p class="muted">No monthly data yet.</p>`;
    return;
  }

  const sources = [...new Set(summaries.flatMap((row) => Object.keys(row.usage)))].sort();
  if (sources.length === 0) {
    container.innerHTML = `<p class="muted">No monthly usage recorded yet.</p>`;
    return;
  }

  const headerCells = [
    "<th>Month</th>",
    `<th>Avg temp (${tempUnitLabel()})</th>`,
    ...sources.map(
      (source) => `<th>${SOURCE_LABELS[source] || source}${units[source] ? ` (${units[source]})` : ""}</th>`
    ),
  ];

  const bodyRows = summaries.map((row) => {
    const temp =
      row.avg_temp_c != null ? fmtNumber(convertTemp(row.avg_temp_c), 1) : "&mdash;";
    const usageCells = sources.map((source) => {
      const value = row.usage[source];
      return `<td>${value != null ? fmtNumber(value) : "&mdash;"}</td>`;
    });
    return `<tr>
      <th scope="row">${formatMonthLabel(row.year, row.month)}</th>
      <td>${temp}</td>
      ${usageCells.join("")}
    </tr>`;
  });

  container.innerHTML = `
    <div class="table-scroll">
      <table class="data-table monthly-table">
        <thead><tr>${headerCells.join("")}</tr></thead>
        <tbody>${bodyRows.join("")}</tbody>
      </table>
    </div>
  `;
}

async function loadTrends(sources) {
  const list = document.getElementById("trend-list");
  list.innerHTML = "";
  if (sources.length === 0) {
    list.innerHTML = `<div class="insight-item">No sources enabled yet.</div>`;
    return;
  }
  let any = false;
  for (const source of sources) {
    try {
      const shifts = await Api.get(`/api/trends?source=${source}`);
      for (const shift of shifts) {
        any = true;
        const item = document.createElement("div");
        item.className = "insight-item";
        const dir = shift.shift > 0 ? "increase" : "decrease";
        item.innerHTML = `<span>${SOURCE_LABELS[source]}: ${dir} around ${fmtDate(shift.date)}</span>
          <span class="muted">z=${fmtNumber(shift.z_score, 2)}</span>`;
        list.appendChild(item);
      }
    } catch (e) {
      // not enough data for this source yet
    }
  }
  if (!any) {
    list.innerHTML = `<div class="insight-item">No significant trend shifts detected yet.</div>`;
  }
}

async function loadEvents() {
  cachedEvents = await Api.get("/api/settings/events");
  const list = document.getElementById("event-list");
  list.innerHTML = "";
  if (cachedEvents.length === 0) {
    list.innerHTML = `<div class="insight-item">No markers yet &mdash; add one on the left.</div>`;
    return;
  }
  for (const event of cachedEvents) {
    const item = document.createElement("div");
    item.className = "insight-item";
    item.innerHTML = `
      <span><strong>${event.title}</strong><br/><span class="muted">${fmtDate(event.event_date)}${event.description ? " &middot; " + event.description : ""}</span></span>
      <button class="secondary" data-id="${event.id}">Delete</button>
    `;
    item.querySelector("button").addEventListener("click", async () => {
      await Api.del(`/api/settings/events/${event.id}`);
      await refreshHistory();
    });
    list.appendChild(item);
  }
}

async function refreshHistory() {
  const [usageBySource, weather, units] = await Promise.all([
    Api.get(`/api/usage?${rangeQuery()}`),
    Api.get(`/api/weather?${rangeQuery()}`),
    Api.get("/api/sources/units"),
  ]);
  const sources = Object.keys(usageBySource);
  await loadMonthlySummaries(units);
  await loadEvents();
  renderUsageWeatherChart("usage-weather-chart", usageBySource, weather, cachedEvents, units);
  await loadTrends(sources);
}

document.addEventListener("DOMContentLoaded", () => {
  flatpickr("#range-input", {
    mode: "range",
    dateFormat: "Y-m-d",
    defaultDate: [currentRange.start, currentRange.end],
    onClose: (selectedDates) => {
      if (selectedDates.length === 2) {
        currentRange = { start: toISODate(selectedDates[0]), end: toISODate(selectedDates[1]) };
        refreshHistory();
      }
    },
  });

  document.getElementById("event-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const event_date = document.getElementById("event-date").value;
    const title = document.getElementById("event-title").value;
    const description = document.getElementById("event-description").value || null;
    await Api.post("/api/settings/events", { event_date, title, description });
    e.target.reset();
    await refreshHistory();
  });

  refreshHistory();
  document.addEventListener("tempunitchange", () => refreshHistory());
});
