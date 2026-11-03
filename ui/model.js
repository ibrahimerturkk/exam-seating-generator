export const policies = {
  mixed: "Mix sections across rooms",
  separate_rooms: "One section per room",
  keep_together: "Keep each section in one room",
};

export function columnLabel(index) {
  let result = "";
  for (let n = index + 1; n; n = Math.floor((n - 1) / 26)) {
    result = String.fromCharCode(65 + (n - 1) % 26) + result;
  }
  return result;
}

export function newRoom(rooms = []) {
  let number = rooms.length + 1;
  while (rooms.some(room => room.name === `Room ${number}`)) number++;
  return { name: `Room ${number}`, rows: 5, columns: 6, student_count: 0,
    aisles_after: [2, 4], blocked_seats: [], seating_policy: "front_first", paper_size: "auto", section: "" };
}

export function available(room) {
  return room.rows * room.columns - room.blocked_seats.length;
}

export function resizeRoom(room, rows, columns) {
  const valid = new Set(Array.from({ length: rows }, (_, row) =>
    Array.from({ length: columns }, (_, col) => `${row + 1}${columnLabel(col)}`)).flat());
  return { ...room, rows, columns,
    aisles_after: room.aisles_after.filter(n => n < columns),
    blocked_seats: room.blocked_seats.filter(code => valid.has(code)) };
}

export function toggleSeat(room, code) {
  const blocked = new Set(room.blocked_seats);
  if (blocked.has(code)) blocked.delete(code); else blocked.add(code);
  return { ...room, blocked_seats: [...blocked] };
}

export function toggleAisle(room, boundary) {
  const aisles = new Set(room.aisles_after);
  if (aisles.has(boundary)) aisles.delete(boundary); else aisles.add(boundary);
  return { ...room, aisles_after: [...aisles].sort((a, b) => a - b) };
}

export function duplicateRoom(room, rooms) {
  let name = `${room.name} (copy)`, number = 2;
  while (rooms.some(item => item.name === name)) name = `${room.name} (copy ${number++})`;
  return { ...structuredClone(room), name };
}

export function safePrefix(course) {
  return course.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9_-]/g, "") || "Exam";
}

export function blankConfig() {
  const now = new Date();
  const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return { course: "", exam_title: "Midterm Exam", date: localDate, time: "09:00",
    seed: crypto.randomUUID(), filename_prefix: "", roster_file: "students.csv", sort_order: "turkish",
    section_policy: "mixed", excluded_student_ids: [], rooms: [newRoom()] };
}

export function exportConfig(state) {
  const result = structuredClone(state);
  result.filename_prefix = result.filename_prefix || safePrefix(result.course);
  if (!result.roster_file) delete result.roster_file;
  if (!result.reuse_assignments_file) delete result.reuse_assignments_file;
  if (result.section_policy === "mixed") result.rooms.forEach(room => { room.section = ""; });
  return result;
}
