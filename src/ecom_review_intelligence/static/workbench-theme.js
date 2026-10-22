/* Theme bootstrap: runs before the stylesheet to avoid a flash of the wrong theme.
   Kept as a separate same-origin file because the workbench CSP forbids inline scripts. */
(function () {
  var stored = null;
  try { stored = localStorage.getItem("wb-theme"); } catch (error) { stored = null; }
  document.documentElement.dataset.theme =
    stored || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
})();
