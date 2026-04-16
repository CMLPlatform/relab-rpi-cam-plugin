const THEME_STORAGE_KEY = "relab-theme";
const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
const themeStorage = window["localStorage"];
const THEME_SEQUENCE = ["auto", "light", "dark"];
const THEME_LABELS = {
  auto: "Auto",
  light: "Light",
  dark: "Dark",
};

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

function syncThemeToggle() {
  const toggle = document.querySelector("[data-theme-toggle]");
  const themePreference = document.documentElement.dataset.themePreference || "auto";

  if (!toggle) {
    return;
  }

  const themeLabel = THEME_LABELS[themePreference] || THEME_LABELS.auto;
  toggle.dataset.themeState = themePreference;
  toggle.setAttribute("aria-label", `Theme: ${themeLabel}`);
  toggle.setAttribute("title", `Theme: ${themeLabel}`);
}

function onSystemThemeChange() {
  const themePreference = themeStorage ? themeStorage.getItem(THEME_STORAGE_KEY) || "auto" : "auto";
  if (themePreference === "auto") {
    applyTheme(themePreference);
    syncThemeToggle();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector("[data-theme-toggle]");
  const themePreference = themeStorage ? themeStorage.getItem(THEME_STORAGE_KEY) || "auto" : "auto";

  applyTheme(themePreference);
  syncThemeToggle();

  if (!toggle) {
    return;
  }

  toggle.addEventListener("click", () => {
    const currentTheme = document.documentElement.dataset.themePreference || "auto";
    const currentIndex = THEME_SEQUENCE.indexOf(currentTheme);
    const nextTheme = THEME_SEQUENCE[(currentIndex + 1) % THEME_SEQUENCE.length];
    if (themeStorage) {
      themeStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    }
    applyTheme(nextTheme);
    syncThemeToggle();
  });
});

if (typeof mediaQuery.addEventListener === "function") {
  mediaQuery.addEventListener("change", onSystemThemeChange);
} else if (typeof mediaQuery.addListener === "function") {
  mediaQuery.addListener(onSystemThemeChange);
}
