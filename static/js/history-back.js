/* Enlaces «Volver» (a[data-back]): si el usuario llegó desde otra pantalla de la app, vuelve atrás en el
   historial (conserva filtros, página y posición del listado); si no (enlace directo, app recién abierta
   o recarga), sigue el href normal. Sin esto, en la PWA instalada no hay botón atrás del navegador. */
document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a[data-back]");
    if (!link || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var ref;
    try { ref = new URL(document.referrer); } catch (err) { return; }
    if (ref.origin !== location.origin || ref.pathname === location.pathname || history.length < 2) return;
    e.preventDefault();
    history.back();
});
