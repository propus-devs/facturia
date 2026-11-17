/* Nombres de archivo largos: una sola línea que se recorta con «…» pero conserva el final
   (últimos caracteres + extensión), que es lo que distingue «Scan_…_194551.jpg» de otro scan.
   Sin esto el navegador partía el nombre en mitad de la extensión («….j» / «pg»).
   Se aplica solo a los enlaces de nombre de las tablas (.name-cell > a) y a los marcados con
   data-fname; otras pantallas pueden llamar a window.fitFilename(elemento). Estilos: .fname en list-page.css. */
(function () {
    var TAIL_STEM = 6;   // caracteres del nombre (sin extensión) que siempre quedan visibles
    var MAX_EXT = 6;     // «.jpeg», «.heic»…: más largo no se considera extensión

    function fit(el) {
        if (!el || el.classList.contains("fname")) return;
        var text = el.textContent.trim();
        if (!text) return;
        var dot = text.lastIndexOf(".");
        var extLen = dot > 0 && text.length - dot <= MAX_EXT ? text.length - dot : 0;
        var cut = Math.max(0, text.length - extLen - TAIL_STEM);

        var head = document.createElement("span");
        head.className = "fname-head";
        head.textContent = text.slice(0, cut);
        var tail = document.createElement("span");
        tail.className = "fname-tail";
        tail.textContent = text.slice(cut);

        if (!el.title) el.title = text;          // nombre completo al pasar el ratón
        el.classList.add("fname");
        el.textContent = "";
        el.append(head, tail);
    }

    window.fitFilename = fit;
    document.querySelectorAll("[data-fname], .name-cell > a, .recent-name").forEach(fit);
})();
