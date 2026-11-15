export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "theme";

/**
 * Runs in <head> before first paint: applies a stored explicit choice. Without one,
 * no data-theme is set and the CSS follows prefers-color-scheme.
 */
export const themeScript = `try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;
