/* Ventas: atajo «N» (ir a [data-new-url]) y apertura/cierre de <dialog>
   con [data-dialog-open="id"] y [data-dialog-close]. */
(function () {
  document.addEventListener("keydown", function (e) {
    if (e.key !== "n" && e.key !== "N") return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
    const el = document.activeElement;
    if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
    if (document.querySelector("dialog[open]")) return;
    const target = document.querySelector("[data-new-url]");
    if (target && target.dataset.newUrl) window.location.href = target.dataset.newUrl;
  });

  document.addEventListener("click", function (e) {
    const opener = e.target.closest("[data-dialog-open]");
    if (opener) {
      const dlg = document.getElementById(opener.dataset.dialogOpen);
      if (dlg && dlg.showModal) dlg.showModal();
      return;
    }
    const closer = e.target.closest("[data-dialog-close]");
    if (closer) { const dlg = closer.closest("dialog"); if (dlg) dlg.close(); return; }
    if (e.target.tagName === "DIALOG") e.target.close();  // clic en el fondo
  });
})();
