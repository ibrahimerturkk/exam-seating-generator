import test from "node:test";
import assert from "node:assert/strict";
import { available, blankConfig, columnLabel, duplicateRoom, exportConfig, newRoom, resizeRoom, safePrefix, toggleAisle, toggleSeat } from "../model.js";

test("spreadsheet-style column labels include multi-letter columns", () => {
  assert.deepEqual([0, 25, 26, 51].map(columnLabel), ["A", "Z", "AA", "AZ"]);
});
test("blocking and unblocking preserve the original room", () => {
  const room = newRoom();
  const blocked = toggleSeat(room, "1A");
  assert.equal(available(blocked), 29);
  assert.deepEqual(room.blocked_seats, []);
  assert.deepEqual(toggleSeat(blocked, "1A").blocked_seats, []);
});
test("aisles toggle at physical column boundaries and remain sorted", () => {
  const room = newRoom();
  assert.deepEqual(toggleAisle(room, 3).aisles_after, [2, 3, 4]);
  assert.deepEqual(toggleAisle(room, 2).aisles_after, [4]);
  assert.deepEqual(room.aisles_after, [2, 4]);
});
test("resizing removes unavailable blocked seats and aisle boundaries", () => {
  const room = { ...newRoom(), blocked_seats: ["1A", "5F", "2D"] };
  const resized = resizeRoom(room, 2, 3);
  assert.deepEqual(resized.blocked_seats, ["1A"]);
  assert.deepEqual(resized.aisles_after, [2]);
  assert.equal(available(resized), 5);
  assert.deepEqual(room.blocked_seats, ["1A", "5F", "2D"]);
});
test("duplicated rooms have unique names and independent layouts", () => {
  const room = newRoom();
  const first = duplicateRoom(room, [room]);
  const second = duplicateRoom(room, [room, first]);
  first.blocked_seats.push("1A");
  assert.equal(first.name, "Room 1 (copy)");
  assert.equal(second.name, "Room 1 (copy 2)");
  assert.deepEqual(room.blocked_seats, []);
  assert.equal(newRoom([newRoom(), { ...newRoom(), name: "Room 3" }]).name, "Room 4");
});
test("any course can be named, including Unicode-only names", () => {
  assert.equal(safePrefix("MATH 101"), "MATH101");
  assert.equal(safePrefix("ÖĞÜ 101"), "OGU101");
  assert.equal(safePrefix("数学"), "Exam");
});
test("new drafts are course-neutral and exports preserve explicit settings", () => {
  const draft = blankConfig();
  assert.equal(draft.course, "");
  draft.course = "BIO 202";
  draft.rooms[0].section = "1";
  draft.reuse_assignments_file = "saved.csv";
  draft.excluded_student_ids = ["001"];
  const exported = exportConfig(draft);
  assert.equal(exported.filename_prefix, "BIO202");
  assert.equal(exported.rooms[0].section, "");
  assert.equal(draft.rooms[0].section, "1");
  assert.deepEqual(exported.excluded_student_ids, ["001"]);
  assert.equal(exported.reuse_assignments_file, "saved.csv");
  draft.section_policy = "keep_together";
  draft.filename_prefix = "Custom";
  assert.equal(exportConfig(draft).rooms[0].section, "1");
  assert.equal(exportConfig(draft).filename_prefix, "Custom");
});
