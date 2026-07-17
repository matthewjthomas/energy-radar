// Small fetch helper shared across pages.
const Api = {
  async get(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`GET ${path} failed: ${resp.status}`);
    return resp.json();
  },
  async post(path, body) {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`POST ${path} failed: ${resp.status}`);
    return resp.json();
  },
  async put(path, body) {
    const resp = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`PUT ${path} failed: ${resp.status}`);
    return resp.json();
  },
  async del(path) {
    const resp = await fetch(path, { method: "DELETE" });
    if (!resp.ok) throw new Error(`DELETE ${path} failed: ${resp.status}`);
    return resp.json();
  },
};

const SOURCE_COLORS = {
  electricity: "#f6c744",
  gas: "#5b8def",
  water: "#38bdf8",
};

const SOURCE_LABELS = {
  electricity: "Electricity",
  gas: "Gas",
  water: "Water",
};

function fmtNumber(n, digits = 1) {
  if (n === null || n === undefined) return "&mdash;";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtDate(d) {
  return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const TEMP_UNIT_KEY = "energyRadarTempUnit";

function getTempUnit() {
  return localStorage.getItem(TEMP_UNIT_KEY) || "F";
}

function setTempUnit(unit) {
  localStorage.setItem(TEMP_UNIT_KEY, unit);
  document.dispatchEvent(new CustomEvent("tempunitchange", { detail: { unit } }));
}

function cToF(celsius) {
  return (celsius * 9) / 5 + 32;
}

// Converts a Celsius value (as stored/returned by the API) into the user's
// currently selected display unit.
function convertTemp(celsius) {
  if (celsius === null || celsius === undefined) return null;
  return getTempUnit() === "F" ? cToF(celsius) : celsius;
}

function tempUnitLabel() {
  return getTempUnit() === "F" ? "\u00b0F" : "\u00b0C";
}

function initTempUnitToggle() {
  const toggle = document.getElementById("temp-unit-toggle");
  if (!toggle) return;
  const buttons = [...toggle.querySelectorAll("button")];
  const applyActive = (unit) => {
    buttons.forEach((btn) => btn.classList.toggle("active", btn.dataset.unit === unit));
  };
  applyActive(getTempUnit());
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      setTempUnit(btn.dataset.unit);
      applyActive(btn.dataset.unit);
    });
  });
}

async function refreshHaStatusPill() {
  const pill = document.getElementById("ha-status-pill");
  if (!pill) return;
  try {
    const status = await Api.get("/api/settings/ha/status");
    if (!status.configured) {
      pill.textContent = "HA: not configured";
      pill.className = "pill pill-unknown";
    } else if (status.connected) {
      pill.textContent = "HA: connected";
      pill.className = "pill pill-ok";
    } else {
      pill.textContent = "HA: unreachable";
      pill.className = "pill pill-bad";
    }
  } catch (e) {
    pill.textContent = "HA: error";
    pill.className = "pill pill-bad";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshHaStatusPill();
  initTempUnitToggle();
});
