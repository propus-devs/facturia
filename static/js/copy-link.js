/* Copia al portapapeles el valor del input indicado en [data-copy-target]. */
(function () {
  function fallback(input) {
    input.focus(); input.select();
    try { return document.execCommand("copy"); } catch (e) { return false; }
  }
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-copy-target]");
    if (!btn) return;
    const input = document.getElementById(btn.dataset.copyTarget);
    if (!input) return;
    const status = btn.closest(".side-card") && btn.closest(".side-card").querySelector("[data-copy-status]");
    const done = function (ok) { if (status) status.textContent = ok ? "Enlace copiado." : "Pulsa Ctrl+C para copiarlo."; };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(function () { done(true); }, function () { done(fallback(input)); });
    } else { done(fallback(input)); }
  });
})();
