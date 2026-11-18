/* Métricas: pestañas, contadores y gráficos con el mismo aspecto que Inicio (ChartKit). */
(function () {
    const K = window.ChartKit;
    K.countUp();

    // --- Botón «Ver»: solo activo cuando las fechas difieren de las cargadas ---
    document.querySelectorAll("form.range").forEach(function (form) {
        const btn = form.querySelector('button[type="submit"]');
        const inputs = form.querySelectorAll('input[type="date"]');
        if (!btn) return;
        const initial = Array.from(inputs, (i) => i.value);
        const sync = function () {
            btn.disabled = Array.from(inputs).every((i, n) => i.value === initial[n]);
        };
        inputs.forEach((i) => i.addEventListener("input", sync));
        sync();
    });

    // --- Pestañas del desglose por origen ---
    document.querySelectorAll("[data-tabs]").forEach(function (box) {
        const tabs = box.querySelectorAll("[data-tab]");
        tabs.forEach(function (btn) {
            btn.addEventListener("click", function () {
                tabs.forEach((b) => {
                    const on = b === btn;
                    b.classList.toggle("active", on);
                    b.setAttribute("aria-selected", on ? "true" : "false");
                });
                box.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== btn.dataset.tab; });
            });
        });
    });

    const dataEl = document.getElementById("metrics-data");
    if (!dataEl || !K.setup()) return;
    const d = JSON.parse(dataEl.textContent);
    const success = K.tone("success"), danger = K.tone("danger"), info = K.tone("info");

    // --- Evolución: ingresos / gastos / beneficio ---
    const evo = document.getElementById("evolutionChart");
    if (evo && d.monthly.length) {
        const ctx = evo.getContext("2d");
        const h = evo.parentElement.clientHeight;
        const line = (label, key, color, extra) => Object.assign({
            label, data: d.monthly.map((r) => r[key]), borderColor: color, backgroundColor: K.gradient(ctx, color, h),
            fill: true, tension: 0.4, borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 6,
        }, extra || {});
        new Chart(ctx, {
            type: "line",
            data: {
                labels: d.monthly.map((r) => r.period),
                datasets: [
                    line("Ingresos", "income", success),
                    line("Gastos", "expense", danger),
                    line("Beneficio", "profit", info, { fill: false, borderDash: [6, 5], borderWidth: 2 }),
                ],
            },
            options: K.cartesian(),
        });
    }

    // --- Por trimestre (barras agrupadas, mismos colores) ---
    const qc = document.getElementById("quartersChart");
    if (qc && d.quarters.length) {
        new Chart(qc.getContext("2d"), {
            type: "bar",
            data: {
                labels: d.quarters.map((q) => q.period),
                datasets: [
                    { label: "Ingresos", data: d.quarters.map((q) => q.income), backgroundColor: success, borderRadius: 6, maxBarThickness: 36 },
                    { label: "Gastos", data: d.quarters.map((q) => q.expense), backgroundColor: danger, borderRadius: 6, maxBarThickness: 36 },
                ],
            },
            options: K.cartesian(),
        });
    }

    // --- Decisiones de revisión (dona con total al centro) ---
    const dc = document.getElementById("decisionsChart");
    if (dc) {
        const center = document.getElementById("donutCenter");
        const values = d.status.map((s) => s.value);
        const total = values.reduce((a, b) => a + b, 0);
        const show = (i) => {
            const num = document.createElement("strong");
            const label = document.createElement("small");
            num.textContent = K.int.format(i == null ? total : values[i]);
            label.textContent = i == null ? "documentos" : d.status[i].label;
            center.replaceChildren(num, label);
        };
        show(null);

        // Leyenda propia (la de Chart.js cambiaba de altura al partirse en filas y descentraba el total)
        const legend = document.getElementById("donutLegend");
        d.status.forEach((s, i) => {
            const li = document.createElement("li");
            li.style.setProperty("--dot", K.tone(s.tone));
            if (!values[i]) li.classList.add("is-zero");
            const dot = document.createElement("i");
            const count = document.createElement("b");
            count.textContent = K.int.format(values[i]);
            li.append(dot, s.label + " ", count);
            li.addEventListener("mouseenter", () => show(i));
            li.addEventListener("mouseleave", () => show(null));
            legend.appendChild(li);
        });
        new Chart(dc.getContext("2d"), {
            type: "doughnut",
            data: {
                labels: d.status.map((s) => s.label),
                datasets: [{ data: values, backgroundColor: d.status.map((s) => K.tone(s.tone)), borderColor: K.css("--card-bg-color"), borderWidth: 3, hoverOffset: 6 }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: "72%",
                animation: K.reduce ? false : { duration: 1000, easing: "easeOutCubic" },
                plugins: {
                    legend: { display: false },
                    tooltip: K.tooltip({ callbacks: { label: (c) => " " + c.label + ": " + c.parsed + " (" + Math.round(c.parsed / total * 100) + " %)" } }),
                },
                onHover: (evt, els) => show(els.length ? els[0].index : null),
            },
        });
        dc.addEventListener("mouseleave", () => show(null));
    }
})();
