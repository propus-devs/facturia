/* Visor de PDF / imagen compartido por el detalle, la rectificación y la pantalla completa.
   Requiere un host [data-file-url][data-file-type="pdf|image"] con un [data-viewer-surface] dentro
   (ver templates/components/_viewer.html). pdf.js solo hace falta para PDF.

   Modelo: un «escenario» centrado en la superficie que se transforma con
   translate · scale · rotate. Zoom 1 = documento entero visible (ajustado al hueco).
   - Zoom: rueda, botones, pellizco con dos dedos, doble clic / doble toque.
   - Mover: arrastrar con ratón o un dedo (con zoom; sin zoom el dedo hace scroll de la página).
   - Girar: pasos de 90°; se recuerda por archivo en este navegador. El archivo guardado no se toca. */
(function () {
    "use strict";

    var MIN_ZOOM = 1;
    var MAX_ZOOM = 6;
    var BUTTON_STEP = 1.25;
    var DOUBLE_TAP_ZOOM = 2.5;
    var PAD = 8;               // margen alrededor del documento a zoom 1 (px)
    var PAN_SLACK = 40;        // cuánto puede pasarse el borde al arrastrar (px)
    var MAX_RENDER_PX = 16e6;  // límite de memoria del canvas de pdf.js

    document.addEventListener("DOMContentLoaded", function () {
        var host = document.querySelector("[data-file-url]");
        var surface = document.querySelector("[data-viewer-surface]");
        if (!host || !surface) return;

        var url = host.dataset.fileUrl;
        var isPdf = host.dataset.fileType === "pdf";
        var fallback = document.getElementById("pdf-fallback");

        var view = { zoom: 1, rot: 0, tx: 0, ty: 0 };
        var natural = { w: 0, h: 0 };   // tamaño del documento sin girar
        var stage = null;               // contenedor transformado (canvas o img)

        // PDF
        var pdfDoc = null, pdfPage = null, pageNum = 1;
        var renderTask = null, renderTimer = null, renderToken = 0;
        var painted = null;             // {page, fit, density} del canvas actual

        var rotKey = "viewer-rot:" + url;
        try {
            var saved = parseInt(localStorage.getItem(rotKey), 10);
            if ([90, 180, 270].indexOf(saved) !== -1) view.rot = saved;
        } catch (e) { /* sin almacenamiento: no se recuerda el giro */ }

        function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

        // ---------- Geometría ----------
        function bbox() {
            var swapped = view.rot % 180 !== 0;
            return { w: swapped ? natural.h : natural.w, h: swapped ? natural.w : natural.h };
        }

        // Escala que hace caber el documento (ya girado) en la superficie
        function fit() {
            var b = bbox();
            var cw = Math.max(surface.clientWidth - PAD * 2, 1);
            var ch = Math.max(surface.clientHeight - PAD * 2, 1);
            return Math.min(cw / b.w, ch / b.h);
        }

        function clampPan() {
            var b = bbox(), f = fit();
            var ex = (b.w * f * view.zoom - surface.clientWidth) / 2;
            var ey = (b.h * f * view.zoom - surface.clientHeight) / 2;
            var mx = ex > 0 ? ex + PAN_SLACK : 0;
            var my = ey > 0 ? ey + PAN_SLACK : 0;
            view.tx = clamp(view.tx, -mx, mx);
            view.ty = clamp(view.ty, -my, my);
        }

        function applyTransform() {
            if (!stage) return;
            stage.style.transform = "translate(-50%, -50%) translate(" + view.tx + "px, " + view.ty +
                "px) scale(" + view.zoom + ") rotate(" + view.rot + "deg)";
            surface.classList.toggle("is-zoomed", view.zoom > 1.01);
        }

        function layout() {
            if (!stage || !natural.w) return;
            var f = fit();
            stage.style.width = natural.w * f + "px";
            stage.style.height = natural.h * f + "px";
            clampPan();
            applyTransform();
        }

        function ensureStage() {
            if (stage) return;
            stage = document.createElement("div");
            stage.className = "viewer-stage";
            surface.appendChild(stage);
        }

        function showFallback() {
            surface.hidden = true;
            if (fallback) fallback.hidden = false;
        }

        // ---------- PDF ----------
        function needsRepaint(f, density) {
            if (!painted || painted.page !== pageNum) return true;
            if (Math.abs(f - painted.fit) / f > 0.01) return true;
            return density > painted.density * 1.05 || density < painted.density / 2;
        }

        function paintPdf(force) {
            if (!pdfPage) return Promise.resolve();
            var f = fit();
            var dpr = Math.min(window.devicePixelRatio || 1, 3);
            var density = dpr * clamp(view.zoom, 1, MAX_ZOOM);
            var cssW = natural.w * f, cssH = natural.h * f;
            density = Math.min(density, Math.sqrt(MAX_RENDER_PX / Math.max(cssW * cssH, 1)));
            if (!force && !needsRepaint(f, density)) return Promise.resolve();

            var token = ++renderToken;
            if (renderTask) { try { renderTask.cancel(); } catch (e) { /* ya terminó */ } }

            var viewport = pdfPage.getViewport({ scale: f * density });
            var canvas = document.createElement("canvas");
            canvas.width = Math.max(Math.floor(viewport.width), 1);
            canvas.height = Math.max(Math.floor(viewport.height), 1);
            var task = pdfPage.render({ canvasContext: canvas.getContext("2d"), viewport: viewport });
            renderTask = task;

            return task.promise.then(function () {
                if (token !== renderToken) return;
                painted = { page: pageNum, fit: f, density: density };
                stage.replaceChildren(canvas);   // se cambia al terminar: sin parpadeo en blanco
            }).catch(function (err) {
                if (err && err.name === "RenderingCancelledException") return;
                console.error("Error renderizando página:", err);
            });
        }

        function scheduleRepaint(delay) {
            if (!isPdf) return;
            clearTimeout(renderTimer);
            renderTimer = setTimeout(function () { paintPdf(false); }, delay === undefined ? 160 : delay);
        }

        function updatePager() {
            var numEl = document.getElementById("page-num");
            var countEl = document.getElementById("page-count");
            var prev = document.getElementById("prev-page");
            var next = document.getElementById("next-page");
            var total = pdfDoc ? pdfDoc.numPages : 0;
            if (numEl) numEl.textContent = pageNum;
            if (countEl) countEl.textContent = total || "–";
            if (prev) prev.disabled = pageNum <= 1;
            if (next) next.disabled = !pdfDoc || pageNum >= total;
        }

        function showPage(num) {
            return pdfDoc.getPage(num).then(function (page) {
                pageNum = num;
                pdfPage = page;
                var vp = page.getViewport({ scale: 1 });   // incluye la rotación propia de la página
                natural = { w: vp.width, h: vp.height };
                ensureStage();
                view.zoom = 1; view.tx = 0; view.ty = 0;
                layout();
                updatePager();
                // Avisa a otras partes de la página (p. ej. las líneas de la factura)
                document.dispatchEvent(new CustomEvent("viewer:page", {
                    detail: { page: num, total: pdfDoc.numPages },
                }));
                return paintPdf(true);
            }).catch(function (err) {
                console.error("Error renderizando página:", err);
            });
        }

        function loadPdf() {
            if (!window.pdfjsLib) { showImage(); return; }
            pdfjsLib.GlobalWorkerOptions.workerSrc =
                "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.9.179/pdf.worker.min.js";
            pdfjsLib.getDocument({ url: url }).promise
                .then(function (pdf) { pdfDoc = pdf; return showPage(1); })
                .catch(function (err) {
                    console.warn("No es un PDF válido, mostrando imagen:", err);
                    showImage();
                });
        }

        // ---------- Imagen ----------
        function showImage() {
            isPdf = false;
            var img = new Image();
            img.alt = "";
            img.draggable = false;
            img.decoding = "async";
            img.onload = function () {
                natural = { w: img.naturalWidth, h: img.naturalHeight };
                ensureStage();
                stage.replaceChildren(img);
                layout();
            };
            img.onerror = showFallback;
            img.src = url;
        }

        // ---------- Zoom / giro ----------
        function rel(clientX, clientY) {
            var r = surface.getBoundingClientRect();
            return { x: clientX - (r.left + r.width / 2), y: clientY - (r.top + r.height / 2) };
        }

        // Cambia el zoom manteniendo fijo el punto (px, py), relativo al centro de la superficie
        function zoomTo(next, px, py) {
            next = clamp(next, MIN_ZOOM, MAX_ZOOM);
            var ratio = next / view.zoom;
            view.tx = px - ratio * (px - view.tx);
            view.ty = py - ratio * (py - view.ty);
            view.zoom = next;
            clampPan();
            applyTransform();
            scheduleRepaint();
        }

        function reset() {
            view.zoom = 1; view.tx = 0; view.ty = 0;
            applyTransform();
            scheduleRepaint(0);
        }

        function rotate(delta) {
            view.rot = (view.rot + delta + 360) % 360;
            view.zoom = 1; view.tx = 0; view.ty = 0;
            try { localStorage.setItem(rotKey, String(view.rot)); } catch (e) { /* opcional */ }
            layout();
            scheduleRepaint(0);
        }

        // ---------- Gestos (ratón, lápiz y táctil) ----------
        var pointers = new Map();   // id -> {x, y, sx, sy, st}
        var gesture = null;
        var lastTap = null;

        function startGesture() {
            var pts = Array.from(pointers.values());
            if (pts.length === 1) {
                gesture = { type: "pan", x: pts[0].x, y: pts[0].y, tx: view.tx, ty: view.ty };
            } else if (pts.length >= 2) {
                var a = pts[0], b = pts[1];
                gesture = {
                    type: "pinch",
                    dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
                    mid: rel((a.x + b.x) / 2, (a.y + b.y) / 2),
                    zoom: view.zoom, tx: view.tx, ty: view.ty,
                };
            } else {
                gesture = null;
            }
        }

        surface.addEventListener("pointerdown", function (e) {
            if (!stage || (e.pointerType === "mouse" && e.button !== 0)) return;
            pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, st: Date.now() });
            try { surface.setPointerCapture(e.pointerId); } catch (err) { /* sin captura */ }
            startGesture();
            if (e.pointerType === "mouse") e.preventDefault();
        });

        surface.addEventListener("pointermove", function (e) {
            var p = pointers.get(e.pointerId);
            if (!p || !gesture) return;
            p.x = e.clientX; p.y = e.clientY;

            if (gesture.type === "pan") {
                view.tx = gesture.tx + (p.x - gesture.x);
                view.ty = gesture.ty + (p.y - gesture.y);
                if (view.zoom > 1.01) surface.classList.add("is-grabbing");
            } else {
                var pts = Array.from(pointers.values());
                var a = pts[0], b = pts[1];
                var dist = Math.hypot(a.x - b.x, a.y - b.y);
                var mid = rel((a.x + b.x) / 2, (a.y + b.y) / 2);
                var next = clamp(gesture.zoom * dist / gesture.dist, MIN_ZOOM, MAX_ZOOM);
                var ratio = next / gesture.zoom;
                // el punto bajo los dedos se queda bajo los dedos (zoom + desplazamiento)
                view.tx = mid.x - ratio * (gesture.mid.x - gesture.tx);
                view.ty = mid.y - ratio * (gesture.mid.y - gesture.ty);
                view.zoom = next;
            }
            clampPan();
            applyTransform();
            if (gesture.type === "pinch") scheduleRepaint();
        });

        function endPointer(e) {
            var p = pointers.get(e.pointerId);
            if (!p) return;
            pointers.delete(e.pointerId);
            surface.classList.remove("is-grabbing");
            try { surface.releasePointerCapture(e.pointerId); } catch (err) { /* ya liberado */ }

            // Doble toque / doble clic: acerca al punto o vuelve al ajuste
            var isTap = e.type === "pointerup" && Math.hypot(e.clientX - p.sx, e.clientY - p.sy) < 10 &&
                Date.now() - p.st < 300 && pointers.size === 0;
            if (isTap) {
                var now = Date.now();
                if (lastTap && now - lastTap.t < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
                    var at = rel(e.clientX, e.clientY);
                    if (view.zoom > 1.05) { reset(); } else { zoomTo(DOUBLE_TAP_ZOOM, at.x, at.y); }
                    lastTap = null;
                } else {
                    lastTap = { t: now, x: e.clientX, y: e.clientY };
                }
            }

            startGesture();   // reanuda con el dedo que quede, sin saltos
            if (pointers.size === 0) { clampPan(); applyTransform(); scheduleRepaint(); }
        }
        surface.addEventListener("pointerup", endPointer);
        surface.addEventListener("pointercancel", endPointer);

        surface.addEventListener("wheel", function (e) {
            e.preventDefault();
            var dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
            var at = rel(e.clientX, e.clientY);
            zoomTo(view.zoom * Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015)), at.x, at.y);
        }, { passive: false });

        // ---------- Barra de herramientas ----------
        function on(id, fn) {
            var el = document.getElementById(id);
            if (el) el.addEventListener("click", fn);
        }
        on("zoom-in", function () { zoomTo(view.zoom * BUTTON_STEP, 0, 0); });
        on("zoom-out", function () { zoomTo(view.zoom / BUTTON_STEP, 0, 0); });
        on("reset-view", reset);
        on("rotate-left", function () { rotate(-90); });
        on("rotate-right", function () { rotate(90); });
        on("prev-page", function () { if (pdfDoc && pageNum > 1) showPage(pageNum - 1); });
        on("next-page", function () { if (pdfDoc && pageNum < pdfDoc.numPages) showPage(pageNum + 1); });

        // Cambios de tamaño (girar el móvil, abrir/cerrar el sidebar, barra del navegador)
        if (window.ResizeObserver) {
            new ResizeObserver(function () { layout(); scheduleRepaint(); }).observe(surface);
        } else {
            window.addEventListener("resize", function () { layout(); scheduleRepaint(); });
        }

        updatePager();
        if (isPdf) { loadPdf(); } else { showImage(); }
    });
})();
