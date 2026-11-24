/* Formulario de factura/presupuesto: líneas dinámicas, totales orientativos y alta rápida de contacto.
   El cálculo es solo informativo; el servidor es la fuente de verdad. */
(function () {
  const body = document.getElementById("lines-body");
  if (!body) return;
  const prefix = document.getElementById("lines").dataset.prefix;
  const totalForms = document.getElementById("id_" + prefix + "-TOTAL_FORMS");
  const template = document.getElementById("line-template");
  const form = document.getElementById("issued-form");
  const fmt = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });

  const num = (v) => {
    const n = parseFloat(String(v || "").trim().replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  };
  const cents = (v) => Math.round((v + Number.EPSILON) * 100) / 100;
  const field = (name) => form.elements[name];
  const SURCHARGE = { 21: 5.2, 10: 1.4, 4: 0.5, 0: 0 };

  function renumber() {
    body.querySelectorAll(".line-row").forEach((row, i) => {
      row.querySelectorAll("input, select, textarea, label").forEach((el) => {
        ["name", "id", "for"].forEach((attr) => {
          const val = el.getAttribute(attr);
          if (val) el.setAttribute(attr, val.replace(new RegExp(prefix + "-(\\d+|__prefix__)-"), prefix + "-" + i + "-"));
        });
      });
    });
    totalForms.value = body.querySelectorAll(".line-row").length;
  }

  function recalc() {
    const gd = Math.min(100, num((field("global_discount_pct") || {}).value));
    const wh = Math.min(100, num((field("withholding_pct") || {}).value));
    const surch = field("apply_surcharge") && field("apply_surcharge").checked;
    const byRate = {};
    let net = 0;
    body.querySelectorAll(".line-row").forEach((row) => {
      const get = (n) => num((row.querySelector('[name$="-' + n + '"]') || {}).value);
      const qty = get("quantity"), price = get("unit_price"), dto = get("discount_pct");
      const rateEl = row.querySelector('[name$="-tax_rate"]');
      const rate = rateEl && rateEl.value.trim() !== "" ? num(rateEl.value) : 21;
      const line = cents(qty * price * (1 - dto / 100));
      net += line;
      byRate[rate] = (byRate[rate] || 0) + line;
    });
    const discount = cents(net * gd / 100);
    let base = 0, vat = 0, rec = 0;
    const vatBox = document.getElementById("totals-vat");
    vatBox.textContent = "";
    Object.keys(byRate).map(Number).sort((a, b) => a - b).forEach((rate) => {
      const b = cents(byRate[rate] * (1 - gd / 100));
      base += b;
      const v = cents(b * rate / 100);
      vat += v;
      if (surch) rec += cents(b * (SURCHARGE[rate] || 0) / 100);
      if (b === 0 && net === 0) return;
      const wrap = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = "IVA " + String(rate).replace(".", ",") + " %";
      const dd = document.createElement("dd");
      dd.textContent = fmt.format(v);
      wrap.append(dt, dd);
      vatBox.appendChild(wrap);
    });
    const ret = cents(base * wh / 100);
    const set = (k, v) => { document.querySelector('[data-total="' + k + '"]').textContent = fmt.format(v); };
    const show = (k, on) => { document.querySelector('[data-row="' + k + '"]').hidden = !on; };
    set("base", base); set("discount", -discount); set("surcharge", rec); set("withholding", -ret);
    set("total", cents(base + vat + rec - ret));
    show("discount", discount > 0); show("surcharge", surch && rec > 0); show("withholding", ret > 0);
  }

  document.getElementById("line-add").addEventListener("click", () => {
    const n = body.querySelectorAll(".line-row").length;
    const html = template.innerHTML.replace(/__prefix__/g, String(n));
    body.insertAdjacentHTML("beforeend", html);
    renumber();
    const last = body.lastElementChild.querySelector("input");
    if (last) last.focus();
    recalc();
  });

  body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-line-remove]");
    if (!btn) return;
    const rows = body.querySelectorAll(".line-row");
    const row = btn.closest(".line-row");
    if (rows.length === 1) {  // siempre queda una fila: se vacía
      row.querySelectorAll("input").forEach((i) => { i.value = ""; });
    } else {
      row.remove();
      renumber();
    }
    recalc();
  });

  form.addEventListener("input", recalc);
  form.addEventListener("change", recalc);
  recalc();

  // ---- Alta rápida de contacto ----
  const dialog = document.getElementById("contact-dialog");
  const cform = document.getElementById("contact-form");
  const err = document.getElementById("contact-error");
  cform.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    const submit = cform.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const resp = await fetch(dialog.dataset.url, {
        method: "POST", body: new FormData(cform), credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await resp.json();
      if (!resp.ok) {
        const msgs = [];
        Object.values(data.errors || {}).forEach((list) => list.forEach((x) => msgs.push(x.message)));
        err.textContent = msgs.join(" ") || "No se pudo guardar el contacto.";
        err.hidden = false;
        return;
      }
      const select = field("company");
      let opt = Array.from(select.options).find((o) => o.value === String(data.id));
      if (!opt) {
        opt = new Option(data.tax_id ? data.name + " (" + data.tax_id + ")" : data.name, data.id);
        select.add(opt);
      }
      select.value = String(data.id);
      cform.reset();
      dialog.close();
    } catch (_) {
      err.textContent = "Error de conexión. Inténtalo de nuevo.";
      err.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
})();
