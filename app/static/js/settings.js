// Settings page logic: HA connection, entity mapping, location, and pricing.

async function loadHaStatus() {
  const line = document.getElementById("ha-status-line");
  try {
    const status = await Api.get("/api/settings/ha/status");
    if (!status.configured) {
      line.textContent = "Not configured. Set HA_URL and HA_TOKEN environment variables and restart the container.";
    } else if (status.connected) {
      line.textContent = "Connected to Home Assistant.";
    } else {
      line.textContent = "HA_URL/HA_TOKEN are set, but the connection failed. Check the values and that Home Assistant is reachable.";
    }
  } catch (e) {
    line.textContent = "Could not check Home Assistant status.";
  }
}

async function loadEntityConfigs() {
  const container = document.getElementById("entity-config-list");
  const configs = await Api.get("/api/settings/ha/entities");
  container.innerHTML = "";
  if (configs.length === 0) {
    container.innerHTML = `<div class="insight-item">No sources mapped yet. Discover sensors above and add them.</div>`;
    return;
  }
  for (const cfg of configs) {
    const row = document.createElement("div");
    row.className = "entity-row";
    row.innerHTML = `
      <strong>${SOURCE_LABELS[cfg.source_type] || cfg.source_type}</strong>
      <span>${cfg.friendly_name || cfg.entity_id}<br/><span class="muted">${cfg.entity_id}</span></span>
      <label class="muted"><input type="checkbox" ${cfg.enabled ? "checked" : ""} data-id="${cfg.id}" class="toggle-enabled" /> enabled</label>
      <button class="secondary" data-id="${cfg.id}">Remove</button>
    `;
    row.querySelector(".toggle-enabled").addEventListener("change", async (e) => {
      await Api.put(`/api/settings/ha/entities/${cfg.id}`, {
        source_type: cfg.source_type,
        entity_id: cfg.entity_id,
        friendly_name: cfg.friendly_name,
        unit: cfg.unit,
        is_cumulative: cfg.is_cumulative,
        enabled: e.target.checked,
      });
    });
    row.querySelector("button").addEventListener("click", async () => {
      await Api.del(`/api/settings/ha/entities/${cfg.id}`);
      await loadEntityConfigs();
    });
    container.appendChild(row);
  }
}

async function loadDiscovered() {
  const container = document.getElementById("discovered-list");
  container.innerHTML = `<div class="insight-item">Loading&hellip;</div>`;
  try {
    const entities = await Api.get("/api/settings/ha/discover");
    container.innerHTML = "";
    if (entities.length === 0) {
      container.innerHTML = `<div class="insight-item">No candidate sensors found.</div>`;
      return;
    }
    for (const entity of entities) {
      const row = document.createElement("div");
      row.className = "insight-item";
      row.innerHTML = `
        <span>${entity.friendly_name}<br/><span class="muted">${entity.entity_id} &middot; ${entity.unit || ""}</span></span>
        <span style="display:flex; gap:8px; align-items:center;">
          <select class="source-select">
            <option value="electricity">Electricity</option>
            <option value="gas">Gas</option>
            <option value="water">Water</option>
          </select>
          <button data-entity="${entity.entity_id}" data-unit="${entity.unit || ""}" data-name="${entity.friendly_name}">Add</button>
        </span>
      `;
      row.querySelector("button").addEventListener("click", async (e) => {
        const btn = e.target;
        const source_type = row.querySelector(".source-select").value;
        await Api.post("/api/settings/ha/entities", {
          source_type,
          entity_id: btn.dataset.entity,
          friendly_name: btn.dataset.name,
          unit: btn.dataset.unit,
          is_cumulative: true,
          enabled: true,
        });
        await loadEntityConfigs();
      });
      container.appendChild(row);
    }
  } catch (e) {
    container.innerHTML = `<div class="insight-item">Could not discover sensors. Is Home Assistant configured and reachable?</div>`;
  }
}

async function loadLocation() {
  const status = document.getElementById("location-status");
  try {
    const location = await Api.get("/api/settings/location");
    if (location) {
      document.getElementById("address-input").value = location.address;
      status.textContent = `Using ${location.address} (${fmtNumber(location.latitude, 3)}, ${fmtNumber(location.longitude, 3)})`;
    }
  } catch (e) {
    // no location set yet
  }
}

async function loadPricing() {
  const configs = await Api.get("/api/settings/pricing");
  for (const cfg of configs) {
    const field = document.getElementById(`price-${cfg.source_type}`);
    if (field) field.value = cfg.price_per_unit;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadHaStatus();
  loadEntityConfigs();
  loadLocation();
  loadPricing();

  document.getElementById("discover-btn").addEventListener("click", loadDiscovered);

  document.getElementById("location-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const address = document.getElementById("address-input").value;
    const status = document.getElementById("location-status");
    status.textContent = "Geocoding...";
    try {
      const location = await Api.post("/api/settings/location", { address });
      status.textContent = `Saved: ${location.address}`;
    } catch (err) {
      status.textContent = "Could not geocode that address.";
    }
  });

  document.getElementById("pricing-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter || document.querySelector("#pricing-form button[type=submit]");
    const status = document.getElementById("pricing-status");
    const originalText = btn ? btn.textContent : null;
    try {
      if (btn) btn.disabled = true;
      for (const source of ["electricity", "gas", "water"]) {
        const field = document.getElementById(`price-${source}`);
        if (field.value === "") continue;
        await Api.post("/api/settings/pricing", {
          source_type: source,
          price_per_unit: parseFloat(field.value),
          currency: "USD",
        });
      }
      if (status) { status.textContent = "Saved!"; status.className = "settings-status ok"; }
    } catch (err) {
      if (status) { status.textContent = "Failed to save prices."; status.className = "settings-status error"; }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = originalText; }
      if (status) setTimeout(() => { status.textContent = ""; status.className = "settings-status"; }, 3000);
    }
  });

  document.getElementById("refresh-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const status = document.getElementById("refresh-status");
    btn.disabled = true;
    btn.textContent = "Refreshing…";
    status.textContent = "";
    status.className = "settings-status";
    try {
      await Api.post("/api/settings/maintenance/refresh", {});
      status.textContent = "Refresh queued — data will update in the background.";
      status.className = "settings-status ok";
    } catch (err) {
      status.textContent = "Refresh failed.";
      status.className = "settings-status error";
    } finally {
      btn.disabled = false;
      btn.textContent = "Refresh data now";
      setTimeout(() => { status.textContent = ""; status.className = "settings-status"; }, 5000);
    }
  });
});
