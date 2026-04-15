const THEME_STORAGE_KEY = "relab-theme";
const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
const themeStorage = window["localStorage"];

function resolveTheme(themePreference) {
  if (themePreference === "dark" || themePreference === "light") {
    return themePreference;
  }
  return mediaQuery.matches ? "dark" : "light";
}

function applyTheme(themePreference) {
  const resolvedTheme = resolveTheme(themePreference);
  document.documentElement.dataset.themePreference = themePreference;
  document.documentElement.dataset.theme = resolvedTheme;
}

function syncThemeSelect() {
  const select = document.querySelector("[data-theme-select]");
  if (!select) {
    return;
  }
  select.value = document.documentElement.dataset.themePreference || "auto";
}

function onSystemThemeChange() {
  const themePreference = themeStorage ? themeStorage.getItem(THEME_STORAGE_KEY) || "auto" : "auto";
  if (themePreference === "auto") {
    applyTheme(themePreference);
    syncThemeSelect();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const select = document.querySelector("[data-theme-select]");
  const themePreference = themeStorage ? themeStorage.getItem(THEME_STORAGE_KEY) || "auto" : "auto";

  applyTheme(themePreference);
  syncThemeSelect();

  if (!select) {
    return;
  }

  select.addEventListener("change", (event) => {
    const nextTheme = event.target.value;
    if (themeStorage) {
      themeStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    }
    applyTheme(nextTheme);
  });
});

if (typeof mediaQuery.addEventListener === "function") {
  mediaQuery.addEventListener("change", onSystemThemeChange);
} else if (typeof mediaQuery.addListener === "function") {
  mediaQuery.addListener(onSystemThemeChange);
}
