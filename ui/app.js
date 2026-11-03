import { available, blankConfig, columnLabel, duplicateRoom, exportConfig, newRoom,
  resizeRoom, safePrefix, toggleAisle, toggleSeat } from "/model.js";

const $ = id => document.getElementById(id);
const storageKey = "exam-room-planner-v1";
let state = blankConfig(), selected = 0, rosterText = "", revision = 0, busy = false, checked = null;
try {
  const draft = JSON.parse(localStorage.getItem(storageKey));
  if (draft?.rooms?.length && draft.rooms.every(room => Number.isInteger(room.rows) && Number.isInteger(room.columns) && Array.isArray(room.blocked_seats) && Array.isArray(room.aisles_after))) state = draft;
} catch { /* Storage is optional; a fresh editor still works. */ }

const help = {
  mixed: "Students are shuffled across all rooms, regardless of section.",
  separate_rooms: "Each room has one section. A large section can use several rooms.",
  keep_together: "Each section stays in a single room, with no other sections in that room.",
};
const examFields = ["course", "exam_title", "date", "time", "roster_file", "filename_prefix", "seed", "sort_order", "reuse_assignments_file"];

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function message(text, type = "info") {
  $("message").textContent = text;
  $("message").className = `message ${type}`;
  $("message").hidden = !text;
}

function changed() {
  revision++;
  checked = null;
  $("check-results").hidden = true;
  $("review-status").textContent = "Changes need checking before you save.";
  $("message").hidden = true;
  try { localStorage.setItem(storageKey, JSON.stringify(state)); $("draft-status").textContent = "Draft saved in this browser"; }
  catch { $("draft-status").textContent = "Unsaved local draft"; }
  renderSummary();
  renderRoomList();
}

function renderSummary() {
  const totals = [state.rooms.length, state.rooms.reduce((n, r) => n + available(r), 0), state.rooms.reduce((n, r) => n + r.student_count, 0)];
  $("overview").replaceChildren(...totals.map((value, i) => {
    const box = node("div"); box.append(node("strong", "", value), node("span", "", ["rooms", "available seats", "students planned"][i])); return box;
  }));
}

function renderRoomList() {
  $("room-list").replaceChildren(...state.rooms.map((room, index) => {
    const button = node("button", "room-tab");
    button.setAttribute("aria-current", String(index === selected));
    const label = node("span"); label.append(node("strong", "", room.name || "Unnamed room"), node("small", "", `${room.rows} × ${room.columns} grid · ${room.student_count} students${room.section && state.section_policy !== "mixed" ? ` · Section ${room.section}` : ""}`));
    button.append(node("span", "room-symbol", "▦"), label, node("span", "chevron", "›"));
    button.addEventListener("click", () => { selected = index; renderRoom(); renderRoomList(); });
    return button;
  }));
}

function renderGrid() {
  const room = state.rooms[selected], grid = $("room-grid");
  const tracks = ["24px"];
  for (let col = 0; col < room.columns; col++) {
    tracks.push("minmax(48px, 1fr)");
    if (col < room.columns - 1) tracks.push(room.aisles_after.includes(col + 1) ? "30px" : "16px");
  }
  grid.style.gridTemplateColumns = tracks.join(" ");
  grid.style.minWidth = `max(100%, ${24 + room.columns * 48 + (room.columns - 1) * 16 + room.aisles_after.length * 14}px)`;
  const children = [];
  function cell(el, row, col) { el.style.gridRow = String(row); el.style.gridColumn = String(col); children.push(el); }
  for (let col = 0; col < room.columns; col++) {
    cell(node("div", "column-head", columnLabel(col)), 1, 2 + col * 2);
    if (col < room.columns - 1) {
      const active = room.aisles_after.includes(col + 1);
      const button = node("button", `aisle-toggle${active ? " active" : ""}`, active ? "−" : "+");
      button.dataset.aisle = String(col + 1);
      button.setAttribute("aria-pressed", String(active));
      button.setAttribute("aria-label", `${active ? "Remove" : "Add"} aisle after column ${columnLabel(col)}`);
      button.title = button.getAttribute("aria-label");
      cell(button, 1, 3 + col * 2);
      if (active) { const line = node("div", "aisle-space"); line.style.gridRow = `2 / span ${room.rows}`; line.style.gridColumn = String(3 + col * 2); children.push(line); }
    }
  }
  const blocked = new Set(room.blocked_seats);
  for (let row = 1; row <= room.rows; row++) {
    cell(node("div", "row-head", row), row + 1, 1);
    for (let col = 0; col < room.columns; col++) {
      const code = `${row}${columnLabel(col)}`, isBlocked = blocked.has(code);
      const button = node("button", `seat${isBlocked ? " blocked" : ""}`, code);
      button.dataset.seat = code;
      button.setAttribute("aria-pressed", String(isBlocked));
      button.setAttribute("aria-label", `Seat ${code}, ${isBlocked ? "blocked" : "available"}. Click to ${isBlocked ? "unblock" : "block"}.`);
      button.title = button.getAttribute("aria-label");
      cell(button, row + 1, 2 + col * 2);
    }
  }
  grid.replaceChildren(...children);
  $("room-capacity").textContent = `${available(room)} available · ${room.blocked_seats.length} blocked`;
  $("student_count").max = String(available(room));
}

function renderPolicy() {
  $("policy-help").textContent = help[state.section_policy];
  $("section-field").hidden = state.section_policy === "mixed";
}

function renderRoom() {
  const room = state.rooms[selected];
  $("layout-heading").textContent = room.name || "Unnamed room";
  $("room-name").value = room.name;
  for (const key of ["rows", "columns", "student_count", "seating_policy", "paper_size", "section"]) $(key).value = room[key];
  $("remove-room").disabled = state.rooms.length === 1;
  renderPolicy(); renderGrid();
}

function renderAll() {
  for (const key of examFields) $(key).value = state[key] || "";
  $("excluded_student_ids").value = (state.excluded_student_ids || []).join("\n");
  $("section_policy").value = state.section_policy;
  renderSummary(); renderRoomList(); renderRoom();
}

function clampCount() {
  const room = state.rooms[selected];
  if (room.student_count > available(room)) {
    room.student_count = available(room);
    $("student_count").value = room.student_count;
    return true;
  }
  return false;
}

for (const key of examFields) $(key).addEventListener("input", event => {
  state[key] = event.target.value;
  changed();
  if (key === "course") $("filename_prefix").placeholder = `Automatic: ${safePrefix(state.course)}`;
});
$("excluded_student_ids").addEventListener("input", event => { state.excluded_student_ids = event.target.value.split(/\s+/).filter(Boolean); changed(); });
$("section_policy").addEventListener("change", event => {
  state.section_policy = event.target.value;
  if (state.section_policy === "mixed") state.rooms.forEach(room => { room.section = ""; });
  changed(); renderRoom();
});
$("room-name").addEventListener("input", event => { state.rooms[selected].name = event.target.value; $("layout-heading").textContent = event.target.value || "Unnamed room"; changed(); });
for (const key of ["rows", "columns"]) $(key).addEventListener("change", () => {
  if (!$(key).reportValidity()) return;
  const room = state.rooms[selected], rows = Number($("rows").value), cols = Number($("columns").value);
  if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 1 || rows > 100 || cols < 1 || cols > 52) return;
  state.rooms[selected] = resizeRoom(room, rows, cols);
  const removed = room.blocked_seats.length - state.rooms[selected].blocked_seats.length;
  const clamped = clampCount(); changed(); renderGrid();
  if (removed || clamped) message(`${removed ? `${removed} blocked seat(s) outside the new grid were removed. ` : ""}${clamped ? "Student count was reduced to the available capacity." : ""}`);
});
$("student_count").addEventListener("input", event => {
  if (event.target.validity.valid && event.target.value !== "") { state.rooms[selected].student_count = Number(event.target.value); changed(); }
});
for (const key of ["seating_policy", "paper_size", "section"]) $(key).addEventListener("input", event => { state.rooms[selected][key] = event.target.value; changed(); });
$("room-grid").addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  const seat = button.dataset.seat, aisle = button.dataset.aisle;
  state.rooms[selected] = seat ? toggleSeat(state.rooms[selected], seat) : toggleAisle(state.rooms[selected], Number(aisle));
  const clamped = clampCount(); changed(); renderGrid();
  $("room-grid").querySelector(seat ? `[data-seat="${seat}"]` : `[data-aisle="${aisle}"]`)?.focus({ preventScroll: true });
  if (clamped) message("Student count was reduced to the available capacity.");
});
$("add-room").addEventListener("click", () => { state.rooms.push(newRoom(state.rooms)); selected = state.rooms.length - 1; changed(); renderRoom(); $("room-name").focus(); });
$("duplicate-room").addEventListener("click", () => { state.rooms.splice(selected + 1, 0, duplicateRoom(state.rooms[selected], state.rooms)); selected++; changed(); renderRoom(); });
$("remove-room").addEventListener("click", () => {
  if (state.rooms.length === 1) return;
  state.rooms.splice(selected, 1); selected = Math.min(selected, state.rooms.length - 1); changed(); renderRoom();
});

async function api(route, body) {
  const response = await fetch(route, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The configuration could not be checked.");
  return data;
}

function setBusy(value) {
  busy = value;
  for (const id of ["check-button", "save-button", "import-button", "fit-button"]) $(id).disabled = value || (id === "fit-button" && !rosterText);
  for (const id of ["roster-upload", "import-file", "clear-roster"]) $(id).disabled = value;
  $("check-button").textContent = value ? "Checking…" : "Check configuration";
}

function showResults(result) {
  $("review-status").textContent = result.roster_checked ? "Verified with your roster. Every student fits and section rules are satisfied." : "Room layouts verified. Upload a roster to also check student totals and sections.";
  if (result.saved_assignments_pending) $("review-status").textContent += " Saved assignments are configured; that file will be checked when generating PDFs.";
  $("check-results").replaceChildren(...result.rooms.map(room => {
    const line = node("div", "result-row");
    line.append(node("strong", "", room.name), node("span", "", `${room.paper} · ${room.available} seats${Object.keys(room.sections).length ? ` · ${Object.entries(room.sections).map(([section, n]) => `Section ${section}: ${n}`).join(", ")}` : ""}`));
    return line;
  }));
  $("check-results").hidden = false;
  $("section-choices").replaceChildren(...Object.keys(result.sections).map(section => { const option = node("option"); option.value = section; return option; }));
}

async function check(fit = false, save = false) {
  if (busy) return;
  for (const input of document.querySelectorAll("input[required], input[type=number]")) if (!input.reportValidity()) return;
  if (fit && !rosterText) return;
  const stamp = revision;
  setBusy(true);
  try {
    const result = await api(fit ? "/api/fit" : "/api/check", { config: exportConfig(state), roster: rosterText });
    if (stamp !== revision) { message("The configuration changed while checking. Please check it again."); return; }
    if (fit) { const autoPrefix = !state.filename_prefix; state = result.config; if (autoPrefix) state.filename_prefix = ""; changed(); renderAll(); }
    checked = result;
    showResults(result);
    message(fit ? "Room counts and section choices now fit your roster. Review them before saving." : "Configuration checked successfully.", "success");
    if (save) { updatePreview(); $("export-dialog").showModal(); }
  } catch (error) { message(error.message === "Failed to fetch" ? "The editor is disconnected. Restart configure.py and try again." : error.message, "error"); }
  finally { setBusy(false); }
}

$("check-button").addEventListener("click", () => check());
$("fit-button").addEventListener("click", () => check(true));
$("save-button").addEventListener("click", () => check(false, true));
$("roster-upload").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 1024 * 1024) { message("Choose a roster smaller than 1 MB.", "error"); event.target.value = ""; return; }
  rosterText = await file.text();
  state.roster_file = file.name; $("roster_file").value = file.name;
  $("roster-status").textContent = `${file.name} loaded. Auto-fit or check to verify it.`;
  $("clear-roster").hidden = false; changed(); setBusy(false);
});
$("clear-roster").addEventListener("click", () => { rosterText = ""; $("roster-upload").value = ""; $("clear-roster").hidden = true; $("roster-status").textContent = "Student data stays in this local session."; changed(); setBusy(false); });
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  setBusy(true);
  try {
    if (file.size > 1024 * 1024) throw new Error("Choose a config smaller than 1 MB.");
    const stamp = revision;
    const result = await api("/api/import", { content: await file.text(), format: file.name.split(".").pop() });
    if (stamp !== revision) throw new Error("The draft changed while importing. Please import again.");
    state = result.config; selected = 0; rosterText = "";
    $("roster-upload").value = ""; $("clear-roster").hidden = true; $("roster-status").textContent = "Student data stays in this local session.";
    changed(); renderAll(); message(`Imported ${file.name}. You can now edit its rooms.`);
  } catch (error) { message(error.message, "error"); }
  finally { event.target.value = ""; setBusy(false); }
});

function exportName() { return `${checked.config.filename_prefix}_${checked.config.date}_config.${$("export-format").value}`; }
function updatePreview() { if (!checked) return; $("config-preview").value = checked[$("export-format").value]; $("export-name").textContent = exportName(); }
$("export-format").addEventListener("change", updatePreview);
$("close-export").addEventListener("click", () => $("export-dialog").close());
$("download-button").addEventListener("click", () => {
  if (!checked) return;
  const format = $("export-format").value;
  const url = URL.createObjectURL(new Blob([checked[format]], { type: format === "json" ? "application/json" : "application/toml" }));
  const link = node("a"); link.href = url; link.download = exportName(); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
renderAll();
