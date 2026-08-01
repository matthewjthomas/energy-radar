// Theme preference: light, dark, or system (follows prefers-color-scheme).
const THEME_KEY = "energyRadarTheme";

function getThemePreference() {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

function applyThemePreference(preference) {
  document.documentElement.setAttribute("data-theme", preference);
}

function setThemePreference(preference) {
  localStorage.setItem(THEME_KEY, preference);
  applyThemePreference(preference);
  document.dispatchEvent(new CustomEvent("themechange", { detail: { theme: preference } }));
}

function initThemeToggle() {
  const toggle = document.getElementById("theme-toggle");
  if (!toggle) return;
  const buttons = [...toggle.querySelectorAll("button")];
  const applyActive = (preference) => {
    buttons.forEach((btn) => btn.classList.toggle("active", btn.dataset.theme === preference));
  };
  applyActive(getThemePreference());
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      setThemePreference(btn.dataset.theme);
      applyActive(btn.dataset.theme);
    });
  });
}

applyThemePreference(getThemePreference());
document.addEventListener("DOMContentLoaded", initThemeToggle);
