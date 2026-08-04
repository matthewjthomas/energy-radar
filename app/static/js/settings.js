// Settings page logic: HA connection, entity mapping, location, and pricing.

const HEATING_FUEL_LABELS = {
  unknown: "Unknown",
  electric: "Electric",
  gas: "Gas",
  heat_pump: "Heat pump",
  dual: "Dual fuel",
};

function heatingFuelOptions(selected = "gas") {
  return Object.entries(HEATING_FUEL_LABELS)
    .map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`)
    .join("");
}

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

async function saveThermostat(payload) {
  await Api.post("/api/settings/thermostat", payload);
  await loadThermostatConfig();
}

async function loadThermostatConfig() {
  const container = document.getElementById("thermostat-config-list");
  const status = document.getElementById("thermostat-status");
  const editPanel = document.getElementById("thermostat-edit-panel");
  if (!container || !status) return;

  let config;
  try {
    config = await Api.get("/api/settings/thermostat");
  } catch (e) {
    config = null;
  }

  container.innerHTML = "";
  if (!config) {
    if (editPanel) editPanel.hidden = true;
    status.textContent = "No thermostat mapped yet. Discover sensors above and add one.";
    return;
  }

  if (editPanel) editPanel.hidden = false;
  const heatingFuel = document.getElementById("thermostat-heating-fuel");
  const coolingFuel = document.getElementById("thermostat-cooling-fuel");
  const gasFraction = document.getElementById("thermostat-gas-fraction");
  const enabled = document.getElementById("thermostat-enabled");
  if (heatingFuel) heatingFuel.value = config.heating_fuel;
  if (coolingFuel) coolingFuel.value = config.cooling_fuel;
  if (gasFraction) gasFraction.value = config.heating_gas_fraction;
  if (enabled) enabled.checked = config.enabled;
  status.textContent = "";

  const row = document.createElement("div");
  row.className = "entity-row";
  row.innerHTML = `
    <strong>Thermostat</strong>
    <span>${config.friendly_name || config.entity_id}<br/><span class="muted">${config.entity_id} &middot; heat: ${HEATING_FUEL_LABELS[config.heating_fuel] || config.heating_fuel}, cool: ${config.cooling_fuel}</span></span>
    <label class="muted"><input type="checkbox" ${config.enabled ? "checked" : ""} class="toggle-thermostat-enabled" /> enabled</label>
    <button type="button" class="secondary thermostat-remove-btn">Remove</button>
  `;
  row.querySelector(".toggle-thermostat-enabled").addEventListener("change", async (e) => {
    await saveThermostat({
      entity_id: config.entity_id,
      friendly_name: config.friendly_name,
      heating_fuel: config.heating_fuel,
      cooling_fuel: config.cooling_fuel,
      heating_gas_fraction: config.heating_gas_fraction,
      enabled: e.target.checked,
    });
  });
  row.querySelector(".thermostat-remove-btn").addEventListener("click", async () => {
    await Api.del("/api/settings/thermostat");
    await loadThermostatConfig();
  });
  container.appendChild(row);
}

async function loadDiscovered() {
  const container = document.getElementById("discovered-list");
  const status = document.getElementById("thermostat-status");
  container.innerHTML = `<div class="insight-item">Loading&hellip;</div>`;
  let existingThermostat = null;
  try {
    existingThermostat = await Api.get("/api/settings/thermostat");
  } catch (e) {
    existingThermostat = null;
  }
  try {
    const entities = await Api.get("/api/settings/ha/discover");
    container.innerHTML = "";
    if (entities.length === 0) {
      container.innerHTML = `<div class="insight-item">No candidate sensors found.</div>`;
      return;
    }
    for (const entity of entities) {
      const row = document.createElement("div");
      row.className = "discover-row";
      if (entity.entity_kind === "climate") {
        row.innerHTML = `
          <span>${entity.friendly_name}<br/><span class="muted">${entity.entity_id} &middot; thermostat</span></span>
          <span class="discover-actions">
            <label class="discover-fuel-label">Heat
              <select class="heating-fuel-select">${heatingFuelOptions("gas")}</select>
            </label>
            <label class="discover-fuel-label">Cool
              <select class="cooling-fuel-select">
                <option value="electric">Electric</option>
                <option value="unknown">Unknown</option>
              </select>
            </label>
            <button type="button" data-entity="${entity.entity_id}" data-name="${entity.friendly_name}">Add thermostat</button>
          </span>
        `;
        const addBtn = row.querySelector("button");
        if (existingThermostat) {
          addBtn.disabled = true;
          addBtn.textContent = "Mapped";
        }
        addBtn.addEventListener("click", async () => {
          addBtn.disabled = true;
          addBtn.textContent = "Adding…";
          try {
            await saveThermostat({
              entity_id: addBtn.dataset.entity,
              friendly_name: addBtn.dataset.name,
              heating_fuel: row.querySelector(".heating-fuel-select").value,
              cooling_fuel: row.querySelector(".cooling-fuel-select").value,
              heating_gas_fraction: 0.5,
              enabled: true,
            });
            if (status) status.textContent = "Thermostat added. Historical readings will sync on the next poll.";
            await loadDiscovered();
          } catch (err) {
            addBtn.disabled = false;
            addBtn.textContent = "Add thermostat";
            if (status) status.textContent = "Could not add thermostat.";
            console.warn("Thermostat add failed", err);
          }
        });
      } else {
        row.innerHTML = `
          <span>${entity.friendly_name}<br/><span class="muted">${entity.entity_id} &middot; ${entity.unit || ""}</span></span>
          <span class="discover-actions">
            <select class="source-select">
              <option value="electricity">Electricity</option>
              <option value="gas">Gas</option>
              <option value="water">Water</option>
            </select>
            <button type="button" data-entity="${entity.entity_id}" data-unit="${entity.unit || ""}" data-name="${entity.friendly_name}">Add</button>
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
      }
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
  loadThermostatConfig();
  loadLocation();
  loadPricing();

  const discoverBtn = document.getElementById("discover-btn");
  if (discoverBtn) discoverBtn.addEventListener("click", loadDiscovered);

  const thermostatForm = document.getElementById("thermostat-form");
  if (thermostatForm) {
    thermostatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const status = document.getElementById("thermostat-status");
      if (!status) return;
      status.textContent = "Saving…";
      try {
        const current = await Api.get("/api/settings/thermostat");
        if (!current) {
          status.textContent = "Add a thermostat from Discover sensors first.";
          return;
        }
        await saveThermostat({
          entity_id: current.entity_id,
          friendly_name: current.friendly_name,
          heating_fuel: document.getElementById("thermostat-heating-fuel")?.value || current.heating_fuel,
          cooling_fuel: document.getElementById("thermostat-cooling-fuel")?.value || current.cooling_fuel,
          heating_gas_fraction: parseFloat(
            document.getElementById("thermostat-gas-fraction")?.value || current.heating_gas_fraction
          ),
          enabled: document.getElementById("thermostat-enabled")?.checked ?? current.enabled,
        });
        status.textContent = "Thermostat settings saved.";
      } catch (err) {
        status.textContent = "Could not save thermostat configuration.";
      }
    });
  }

  const locationForm = document.getElementById("location-form");
  if (locationForm) {
    locationForm.addEventListener("submit", async (e) => {
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
  }

  const pricingForm = document.getElementById("pricing-form");
  if (pricingForm) {
    pricingForm.addEventListener("submit", async (e) => {
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
  }

  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const status = document.getElementById("refresh-status");
      btn.disabled = true;
      btn.textContent = "Refreshing…";
      status.textContent = "";
      status.className = "settings-status";
      const minDelay = new Promise((r) => setTimeout(r, 1500));
      try {
        await Promise.all([Api.post("/api/settings/maintenance/refresh", {}), minDelay]);
        status.textContent = "Done — data is updating in the background.";
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
  }

  const forecastResetBtn = document.getElementById("forecast-reset-btn");
  if (forecastResetBtn) {
    forecastResetBtn.addEventListener("click", async (e) => {
      const confirmed = window.confirm(
        "Clear all stored forecast scores and learned bias? Live forecasts will rebuild without historical calibration."
      );
      if (!confirmed) return;

      const btn = e.currentTarget;
      const status = document.getElementById("forecast-reset-status");
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Resetting…";
      status.textContent = "";
      status.className = "settings-status";
      try {
        await Api.post("/api/settings/maintenance/forecast/reset", {});
        status.textContent = "Forecast calibration cleared.";
        status.className = "settings-status ok";
      } catch (err) {
        status.textContent = "Reset failed.";
        status.className = "settings-status error";
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
        setTimeout(() => { status.textContent = ""; status.className = "settings-status"; }, 5000);
      }
    });
  }
});
