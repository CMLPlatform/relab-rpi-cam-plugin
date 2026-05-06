(() => {
  const storageKey = "relab-theme";
  const themeStorage = window.localStorage;
  const storedTheme = themeStorage ? themeStorage.getItem(storageKey) : null;
  const preferredTheme = storedTheme || "auto";
  const resolvedTheme =
    preferredTheme === "auto"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preferredTheme;

  document.documentElement.dataset.themePreference = preferredTheme;
  document.documentElement.dataset.theme = resolvedTheme;
})();
