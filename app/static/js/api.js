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

document.addEventListener("DOMContentLoaded", refreshHaStatusPill);
