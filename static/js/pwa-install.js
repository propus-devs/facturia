/* Cuenta: botón «Instalar acceso directo» (PWA sin service worker).
   - Chrome/Edge/Android: lanza el cuadro de instalación del navegador.
   - iPhone/iPad y navegadores sin ese cuadro: muestra los pasos manuales.
   - Si la web ya se abre como app instalada, lo indica y oculta el botón. */
(function () {
  "use strict";

  var card = document.querySelector("[data-pwa-install]");
  if (!card) return;

  var btn = card.querySelector("[data-pwa-btn]");
  var status = card.querySelector("[data-pwa-status]");
  var help = card.querySelector("[data-pwa-help]");
  var deferredPrompt = null;

  var ua = navigator.userAgent || "";
  var isIOS = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isAndroid = /Android/.test(ua);

  function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone === true;
  }

  function helpText() {
    if (isIOS) return "En Safari pulsa el botón Compartir (el cuadrado con la flecha hacia arriba) y elige «Añadir a pantalla de inicio». Debe ser Safari, no otro navegador.";
    if (isAndroid) return "En Chrome abre el menú ⋮ y elige «Instalar app» o «Añadir a pantalla de inicio».";
    return "En Chrome o Edge pulsa el icono de instalar de la barra de direcciones, o abre el menú ⋮ → «Guardar y compartir» → «Crear acceso directo» (marca «Abrir como ventana»). Firefox y Safari de escritorio no permiten instalar webs.";
  }

  function showHelp() {
    help.textContent = helpText();
    help.hidden = false;
  }

  function markInstalled() {
    btn.hidden = true;
    help.hidden = true;
    status.textContent = "FacturIA ya está instalada como aplicación en este dispositivo.";
  }

  if (isStandalone()) {
    markInstalled();
    return;
  }

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    status.textContent = "Tu navegador permite instalarla con un clic.";
  });

  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    markInstalled();
  });

  btn.addEventListener("click", function () {
    if (!deferredPrompt) {
      showHelp();
      return;
    }
    var promptEvent = deferredPrompt;
    deferredPrompt = null;
    promptEvent.prompt();
    promptEvent.userChoice.then(function (choice) {
      if (choice && choice.outcome === "accepted") {
        markInstalled();
      } else {
        status.textContent = "Instalación cancelada. Puedes volver a intentarlo cuando quieras.";
      }
    });
  });
})();
