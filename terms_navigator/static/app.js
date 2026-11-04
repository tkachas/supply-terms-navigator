// Клиент без сборки и зависимостей. Состояние — загруженные файлы и дата проверки;
// всё извлечение и сверка выполняются на сервере (POST /api/analyze, /api/ask).
"use strict";

const state = {
  files: new Map(),      // name -> base64
  checkDate: "2026-03-10",
  analysis: null,
  expanded: new Set(),
  lastQuestion: null,
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = (iso) => (iso ? iso.split("-").reverse().join(".") : "—");

// ---------- загрузка файлов ----------

function toBase64(buf) {
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function addFiles(fileList) {
  const skipped = [];
  for (const f of fileList) {
    if (!/\.txt$/i.test(f.name)) { skipped.push(f.name); continue; }
    state.files.set(f.name, toBase64(await f.arrayBuffer()));   // одноимённый файл заменяется
  }
  if (skipped.length) alert("Пропущены файлы не в формате TXT: " + skipped.join(", "));
  await refresh();
}

async function loadSamples() {
  const r = await fetch("/api/samples");
  const data = await r.json();
  for (const f of data.files) state.files.set(f.name, f.content_b64);
  await refresh();
}

function payload(extra = {}) {
  return JSON.stringify({
    files: [...state.files].map(([name, content_b64]) => ({ name, content_b64 })),
    check_date: state.checkDate,
    ...extra,
  });
}

async function api(path, extra) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: payload(extra) });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

async function refresh() {
  state.analysis = state.files.size ? await api("/api/analyze") : null;
  render();
  if (state.lastQuestion && state.analysis) ask(state.lastQuestion);
  else $("#answer").innerHTML = "";
}

// ---------- ссылки на источник ----------

function srcLink(ev, text) {
  if (!ev) return "";
  // Основание хранится прямо в атрибуте: никаких индексов, которые могут разъехаться
  // при повторной отрисовке.
  const where = ev.label || `абз. ${ev.para + 1}`;
  return `<button class="src" data-ev="${esc(JSON.stringify(ev))}" title="${esc(ev.quote)}">📄 ${esc(text || `${ev.file} · ${where}`)}</button>`;
}

function docById(id) { return state.analysis.documents.find((d) => d.doc_id === id); }

function showSource(ev) {
  const doc = docById(ev.doc_id);
  if (!doc) return;
  const box = $("#source");
  const paras = doc.paragraphs.map((p) => {
    let html = esc(p.text);
    if (p.idx === ev.para) {
      const t = p.text;
      const vs = ev.value_start, ve = ev.value_end;
      const parts = [];
      // подсветка: цитата-основание + само значение внутри неё
      parts.push(esc(t.slice(0, ev.start)));
      if (vs != null && vs >= ev.start && ve <= ev.end) {
        parts.push("<mark>" + esc(t.slice(ev.start, vs)) + "</mark>");
        parts.push('<mark class="value">' + esc(t.slice(vs, ve)) + "</mark>");
        parts.push("<mark>" + esc(t.slice(ve, ev.end)) + "</mark>");
      } else {
        parts.push("<mark>" + esc(t.slice(ev.start, ev.end)) + "</mark>");
      }
      parts.push(esc(t.slice(ev.end)));
      html = parts.join("");
    }
    const cls = ["para", p.header ? "header" : "", p.idx === ev.para ? "target" : ""].join(" ");
    return `<div class="${cls}" id="p-${p.idx}"><span class="lbl">${esc(p.label || "")}</span><span>${html}</span></div>`;
  }).join("");
  box.innerHTML = `
    <h3>${esc(doc.file)}</h3>
    <div class="docmeta"><span class="tag kind">${esc(doc.kind_label)}</span><span>${esc(doc.sign_label)}</span>
      ${doc.effective_from ? `<span>в силе с ${fmtDate(doc.effective_from)}</span>` : ""}</div>
    ${paras}`;
  const target = box.querySelector(`#p-${ev.para}`);
  if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
}

function showDoc(docId) {
  const d = docById(docId);
  if (d) showSource({ doc_id: d.doc_id, file: d.file, para: -1, start: 0, end: 0 });
  $("#source").scrollTop = 0;
}

// ---------- рендер ----------

const STATE_TAG = {
  amended: '<span class="tag amended">изменено ДС</span>',
  no_data: '<span class="tag no_data">нет данных</span>',
  needs_review: '<span class="tag needs_review">требует проверки</span>',
  original: "",
};

function renderDocs() {
  const a = state.analysis;
  $("#docList").innerHTML = !a ? "" : a.documents.map((d) => {
    const refs = d.kind === "contract" ? d.number : (d.contract_refs.join(", ") || "договор не найден");
    return `<li data-doc="${d.doc_id}" title="${esc(d.title)}">
      <span class="tag kind">${esc(d.kind_label)}</span>
      <span><div class="fname">${esc(d.file)}</div><div class="meta">${esc(refs)} · ${esc(d.sign_label)}</div></span>
      <button class="rm" data-rm="${esc(d.file)}" title="Убрать файл">×</button></li>`;
  }).join("");
}

function fieldCell(f) {
  const prev = f.history.length ? `<span class="prev">${esc(f.history[0].display)}</span>` : "";
  const val = f.state === "no_data" ? '<span class="val no_data">нет данных</span>' : `<span class="val">${esc(f.display)}</span>`;
  return `${val}${prev}
    <div class="cell-meta">${STATE_TAG[f.state]}${srcLink(f.evidence)}</div>
    ${f.notes.filter((n) => f.state !== "original" && f.state !== "amended").map((n) => `<div class="muted" style="font-size:12px">${esc(n)}</div>`).join("")}`;
}

function pendingCell(c) {
  if (!c.pending.length) return '<span class="muted">—</span>';
  return c.pending.map((p) => `<div class="pend"><b>${esc(p.field_title)}: ${esc(p.proposed)}</b>
    <span class="muted">(сейчас ${esc(p.current)})</span><br>
    <span class="tag pending">${esc(p.kind_label)}</span> ${srcLink(p.evidence)}</div>`).join("");
}

function detailsRow(c) {
  const related = c.related.length
    ? c.related.map((r) => { const d = docById(r.doc_id);
        return `<li><span class="${r.applied ? "ok-mark" : "no-mark"}">${r.applied ? "✔" : "✕"}</span>
          <a href="#" data-doc="${d.doc_id}">${esc(d.file)}</a> — ${esc(d.title)}<br><span class="muted">${esc(r.verdict)}</span></li>`; }).join("")
    : '<li class="muted">Связанных документов нет</li>';
  const fieldsInfo = Object.values(c.fields).map((f) => {
    const items = [];
    f.history.forEach((h) => items.push(`<li>Прежнее значение: ${esc(h.display)} ${srcLink(h.evidence)}</li>`));
    f.other_mentions.forEach((m) => items.push(`<li>Не использовано: ${esc(m.display)} — ${esc(m.note)} ${srcLink(m.evidence)}</li>`));
    f.notes.forEach((n) => items.push(`<li>${esc(n)}</li>`));
    return items.length ? `<h4>${esc(f.title)}</h4><ul>${items.join("")}</ul>` : "";
  }).join("");
  const issues = c.issues.length
    ? `<h4>Замечания</h4><ul>${c.issues.map((i) => `<li>${i.severity === "review" ? "⚠ " : ""}${esc(i.message)} ${srcLink(i.evidence)}</li>`).join("")}</ul>` : "";
  return `<tr class="details"><td colspan="6">
    <h4>Связанные документы</h4><ul>${related}</ul>${fieldsInfo}${issues}</td></tr>`;
}

function renderContracts() {
  const a = state.analysis;
  const body = $("#contracts tbody");
  $("#emptyHint").hidden = !!(a && a.contracts.length);
  $("#dateLabel").textContent = fmtDate(state.checkDate);
  if (!a) { body.innerHTML = ""; return; }
  body.innerHTML = a.contracts.map((c) => {
    const open = state.expanded.has(c.contract_no);
    const row = `<tr class="contract-row ${c.needs_review ? "review" : ""}" data-contract="${esc(c.contract_no)}">
      <td><span class="val">${esc(c.contract_no)}</span><div class="cell-meta"><a href="#" class="src" data-doc="${c.doc_id}">📄 ${esc(docById(c.doc_id).file)}</a></div></td>
      <td>${esc(c.fields.supplier.display)}</td>
      <td>${fieldCell(c.fields.amount)}</td>
      <td>${fieldCell(c.fields.delivery_date)}</td>
      <td>${pendingCell(c)}</td>
      <td>${c.needs_review ? '<span class="tag needs_review">проверить</span>' : ""}<div class="muted" style="font-size:12px">${open ? "▲ свернуть" : "▼ подробнее"}</div></td>
    </tr>`;
    return row + (open ? detailsRow(c) : "");
  }).join("");
}

function renderIssues() {
  const a = state.analysis;
  const list = a ? a.issues : [];
  $("#globalIssues").innerHTML = list.length
    ? `<div class="issues"><b>Требует внимания</b><ul>${list.map((i) => {
        const d = i.doc_id ? docById(i.doc_id) : null;
        return `<li>${d ? `<a href="#" data-doc="${d.doc_id}">${esc(d.file)}</a>: ` : ""}${esc(i.message)}</li>`; }).join("")}</ul></div>`
    : "";
}

function renderQuestionControls() {
  const a = state.analysis;
  const contracts = a ? a.contracts.map((c) => c.contract_no) : [];
  const sel = $("#qContract");
  const prev = sel.value;
  sel.innerHTML = contracts.map((n) => `<option>${esc(n)}</option>`).join("");
  if (contracts.includes(prev)) sel.value = prev;
  const qt = $("#qType").value;
  sel.hidden = qt === "deliveries_before";
  $("#qBefore").hidden = qt !== "deliveries_before";

  // Пресеты — четыре вопроса из задания: только параметры запроса (номер договора, дата),
  // ответы считаются по загруженным данным. Произвольный вопрос — через конструктор ниже.
  const d = fmtDate(state.checkDate);
  const presets = [
    [`Какой срок поставки действует по договору STN-101 на ${d}?`, { type: "delivery_date", contract: "STN-101" }],
    ["Какова сумма договора STN-102?", { type: "amount", contract: "STN-102" }],
    ["Какова сумма договора STN-103?", { type: "amount", contract: "STN-103" }],
    ["По каким договорам поставка раньше 01.04.2026 и есть ли несогласованные изменения срока?", { type: "deliveries_before", before: "2026-04-01" }],
  ];
  $("#presets").innerHTML = presets.map(([label, q], i) =>
    `<button class="btn preset" data-preset="${i}" ${a && (!q.contract || contracts.includes(q.contract)) ? "" : "disabled"}
      title="${q.contract && !contracts.includes(q.contract) ? "Договор не загружен" : ""}">${esc(label)}</button>`).join("");
  $("#presets").onclick = (e) => {
    const b = e.target.closest("[data-preset]");
    if (b) ask(presets[+b.dataset.preset][1]);
  };
}

function render() {
  renderDocs();
  renderIssues();
  renderContracts();
  renderQuestionControls();
}

async function ask(question) {
  state.lastQuestion = question;
  const box = $("#answer");
  try {
    const ans = await api("/api/ask", { question });
    box.innerHTML = `<div class="answer ${ans.status}">
      <div class="q">${esc(ans.question)}</div>
      <div class="headline">${esc(ans.headline)}</div>
      <ul>${ans.lines.map((l) => `<li class="${l.tone}">${esc(l.text)} ${l.evidence.map((e) => srcLink(e)).join(" ")}</li>`).join("")}</ul>
    </div>`;
  } catch (e) {
    box.innerHTML = `<div class="answer not_found">Ошибка: ${esc(e.message)}</div>`;
  }
}

// ---------- события ----------

document.addEventListener("click", (e) => {
  const src = e.target.closest("[data-ev]");
  if (src) { e.stopPropagation(); showSource(JSON.parse(src.dataset.ev)); return; }
  const rm = e.target.closest("[data-rm]");
  if (rm) { e.stopPropagation(); state.files.delete(rm.dataset.rm); refresh(); return; }
  const doc = e.target.closest("[data-doc]");
  if (doc) { e.preventDefault(); showDoc(doc.dataset.doc); return; }
  const row = e.target.closest("tr.contract-row");
  if (row) {
    const n = row.dataset.contract;
    state.expanded.has(n) ? state.expanded.delete(n) : state.expanded.add(n);
    renderContracts();
  }
});

$("#fileInput").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
$("#loadSamples").addEventListener("click", loadSamples);
$("#clearAll").addEventListener("click", () => { state.files.clear(); state.lastQuestion = null; refresh(); });
$("#checkDate").addEventListener("change", (e) => { if (e.target.value) { state.checkDate = e.target.value; refresh(); } });
$("#qType").addEventListener("change", renderQuestionControls);
$("#qAsk").addEventListener("click", () => {
  const type = $("#qType").value;
  ask(type === "deliveries_before" ? { type, before: $("#qBefore").value } : { type, contract: $("#qContract").value });
});

const dz = $("#dropzone");
["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

$("#checkDate").value = state.checkDate;
render();
