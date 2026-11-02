/* Vista COT existente: no calcula scores ni altera el bucle del agente. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const node = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const number = n => Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toLocaleString("es-ES")}` : "—";
  const day = value => value ? new Date(`${value}T12:00:00Z`).toLocaleDateString("es-ES", {day:"2-digit", month:"short", year:"numeric", timeZone:"UTC"}) : "—";
  const participants = {MANAGED_MONEY:"Managed Money", LEVERAGED_FUNDS:"Leveraged Funds"};
  const filters = [["ALL","Todos"],["FX","FX"],["METALS","Metales"],["ENERGY","Energía"],["CRYPTO","Cripto"]];
  let items = [], category = "ALL", selected = null, count = 52, initialized = false, loading = false;

  function initialize() {
    if (initialized) return;
    initialized = true;
    for (const [key, label] of filters) {
      const button = node("button", key === category ? "on" : "", label);
      button.type = "button"; button.dataset.category = key;
      button.setAttribute("aria-pressed", String(key === category));
      button.onclick = () => { category = key; render(); };
      $("positioning-filters").append(button);
    }
    for (const size of [13,26,52]) {
      const button = node("button", size === count ? "on" : "", `${size} informes`);
      button.type = "button"; button.dataset.count = size;
      button.onclick = () => { count = size; detail(); };
      $("positioning-ranges").append(button);
    }
    $("positioning-reload").onclick = load;
  }

  async function load() {
    initialize();
    if (loading) return;
    loading = true;
    $("positioning-reload").disabled = true;
    $("positioning-status").classList.remove("error");
    $("positioning-status").textContent = "Consultando los informes COT…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch("/api/cot/overview", {signal:controller.signal});
      if (!response.ok) throw new Error("HTTP");
      const data = await response.json();
      if (data.api_version !== "cot-overview-v1" || !Array.isArray(data.items)) throw new Error("CONTRACT");
      items = data.items;
      const available = items.filter(item => item.status !== "UNAVAILABLE").length;
      const retained = items.filter(item => item.status === "LAST_VALID").length;
      $("positioning-status").textContent = available
        ? `${available} de ${items.length} series con datos · hasta ${data.history_limit} informes por serie${retained ? " · hay actualizaciones fallidas; se conserva el último dato válido" : ""}.`
        : "La CFTC no ha devuelto datos disponibles. Puedes volver a consultar esta vista más tarde.";
      $("positioning-status").classList.toggle("error", !available || !!retained);
      render();
    } catch {
      $("positioning-status").classList.add("error");
      $("positioning-status").textContent = items.length
        ? "No se pudo actualizar. Sigues viendo los datos de la consulta anterior; sus fechas no han cambiado."
        : "No se pudo cargar el COT. Pulsa Actualizar vista para reintentar.";
    } finally {
      clearTimeout(timeout); loading = false; $("positioning-reload").disabled = false;
    }
  }

  function render() {
    for (const button of $("positioning-filters").children) {
      const active = button.dataset.category === category;
      button.classList.toggle("on", active); button.setAttribute("aria-pressed", String(active));
    }
    const visible = items.filter(item => category === "ALL" || item.category === category);
    if (!visible.some(item => item.key === selected)) selected = (visible.find(item => item.history.length) || visible[0])?.key;
    $("positioning-rows").replaceChildren();
    for (const item of visible) {
      const row = node("tr", item.key === selected ? "selected" : "");
      const asset = node("td");
      const button = node("button", "positioning-asset", item.label);
      button.type = "button"; button.setAttribute("aria-pressed", String(item.key === selected));
      button.onclick = () => { selected = item.key; render(); };
      asset.append(button);
      const family = node("td", "", item.report_type);
      family.append(node("span", "positioning-participant", participants[item.participant_category] || item.participant_category));
      const ageText = item.status === "UNAVAILABLE" ? "No disponible"
        : item.status === "LAST_VALID" ? `Actualización fallida · ${item.age_days} d`
        : item.age_warning ? `Informe antiguo · ${item.age_days} d` : `${item.age_days} días`;
      const age = node("td"); age.append(node("span", "positioning-age"+(item.age_warning || item.status !== "AVAILABLE" ? " warning" : ""), ageText));
      row.append(asset, family, node("td", "", number(item.net)), node("td", "", number(item.weekly_change)), node("td", "positioning-date", day(item.report_date)), age);
      $("positioning-rows").append(row);
    }
    detail();
  }

  function detail() {
    const item = items.find(item => item.key === selected);
    $("positioning-detail").hidden = !item;
    if (!item) return;
    $("positioning-family").textContent = `${item.report_type} · ${participants[item.participant_category]} · Futures Only`;
    $("positioning-title").textContent = item.label;
    $("positioning-note").textContent = item.note;
    $("positioning-source").href = item.source_url;
    $("positioning-values").replaceChildren();
    const stats = [[number(item.net),"Neto · contratos"],[number(item.weekly_change),"Cambio · 1 semana"],[day(item.report_date),"Posiciones a"]];
    for (const [value,label] of stats) {
      const block = node("div", "positioning-value");
      block.append(node("strong", "", value), node("span", "", label));
      $("positioning-values").append(block);
    }
    if (item.net !== null && item.weekly_change === null) {
      $("positioning-values").append(node("p", "positioning-footnote", "Cambio semanal no disponible: falta un informe válido exactamente una semana antes."));
    }
    for (const button of $("positioning-ranges").children) {
      const active = Number(button.dataset.count) === count;
      button.classList.toggle("on", active); button.setAttribute("aria-pressed", String(active));
    }
    draw(item.history.slice(-count));
  }

  function draw(points) {
    const holder = $("positioning-chart"); holder.replaceChildren();
    $("positioning-history").replaceChildren();
    $("positioning-readout").textContent = "";
    if (!points.length) {
      holder.append(node("p", "positioning-empty", "Sin histórico disponible para esta serie. La ausencia de datos no indica una posición neutral."));
      return;
    }
    const svgNode = (tag, attrs, text) => {
      const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key,value] of Object.entries(attrs)) e.setAttribute(key,String(value));
      if (text !== undefined) e.textContent = text;
      return e;
    };
    const svg = svgNode("svg", {viewBox:"0 0 820 280",role:"group","aria-label":"Histórico de posiciones netas en contratos"});
    const max = Math.max(1,...points.map(point => Math.abs(point.net)));
    const start = Date.parse(points[0].date), end = Date.parse(points.at(-1).date);
    const span = Math.max(7*86400000,end-start);
    const x = point => 66 + (Date.parse(point.date)-start)/span*734;
    const gaps = points.slice(1).map((point,i) => x(point)-x(points[i]));
    const width = Math.max(1,Math.min(18, ...(gaps.length ? gaps.map(gap=>gap*.7) : [18])));
    for (const [value,y] of [[max,24],[0,134],[-max,244]]) {
      svg.append(svgNode("line",{x1:58,x2:812,y1:y,y2:y,stroke:value===0?"#8a8479":"#e0d9cc","stroke-width":1}));
      svg.append(svgNode("text",{x:50,y:y+4,"text-anchor":"end","font-size":10,fill:"#54524b"}, new Intl.NumberFormat("es-ES",{notation:"compact",maximumFractionDigits:1}).format(value)));
    }
    const readout = point => { $("positioning-readout").textContent = `${day(point.date)} · ${number(point.net)} contratos`; };
    for (const point of points) {
      const height = Math.abs(point.net)/max*110;
      const rect = svgNode("rect",{x:x(point)-width/2,y:point.net>=0?134-height:134,width,height:Math.max(1,height),fill:point.net>=0?"#b99736":"#bd715e",class:"cot-point",tabindex:0,role:"img","aria-label":`${day(point.date)}: ${number(point.net)} contratos`});
      rect.append(svgNode("title",{},`${day(point.date)}: ${number(point.net)} contratos`));
      rect.onmouseenter = () => readout(point); rect.onfocus = () => readout(point);
      svg.append(rect);
    }
    svg.onmouseleave = () => readout(points.at(-1));
    svg.append(svgNode("text",{x:58,y:275,"font-size":11,fill:"#54524b"},day(points[0].date)));
    svg.append(svgNode("text",{x:812,y:275,"text-anchor":"end","font-size":11,fill:"#54524b"},day(points.at(-1).date)));
    holder.append(svg); readout(points.at(-1));
    const table = node("table");
    const head = node("thead"), header = node("tr");
    for (const label of ["Fecha de posiciones","Neto · contratos"]) {const th=node("th","",label); th.scope="col"; header.append(th);}
    head.append(header); table.append(head);
    const body = node("tbody");
    for (const point of [...points].reverse()) {const row=node("tr"); row.append(node("td","",day(point.date)),node("td","",number(point.net)));body.append(row);}
    table.append(body); $("positioning-history").append(table);
  }

  window.PositioningView = {load};
})();
