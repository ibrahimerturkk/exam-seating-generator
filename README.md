# Exam seating generator

A portable exam seating tool with a visual config editor and command-line PDF generation.
Use it for any course. Change the roster and settings for each exam. Existing JSON configs also work.
No Codex, Excel installation, or operating-system-specific fonts are required.

## Setup

Use Python 3.10, 3.11, or 3.12. Run these commands inside this folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use `python` instead of `python3`, then activate with
`.venv\Scripts\activate` in Command Prompt or `.venv\Scripts\Activate.ps1`
in PowerShell.

## Visual room and exam editor

Start the editor from this folder:

```sh
python configure.py
```

It opens `http://127.0.0.1:8765` in your browser. With uv, use
`uv run --locked python configure.py` or `make ui`. No frontend build, Node.js,
account, or internet connection is needed to use the editor. Stop it with Ctrl+C.
Use `--no-browser` to open the printed URL yourself, or `--port 8766` if the
default port is occupied.

1. Enter any **course name**, exam title, date, and time, then choose a section rule.
2. Add, name, or duplicate rooms. Set rows, seats per row, and student counts.
3. Click **+ / − between column labels** to add or remove aisles. Click seats to
   block or unblock them. Resize grids to change the layout.
4. Optionally upload a roster CSV. **Check configuration** verifies totals and
   section rules. **Auto-fit students to rooms** suggests counts using each
   room's available seats and the selected section rule.
5. Choose **Save config**, review the preview, and download TOML or JSON.
   Shared room settings are automatically extracted into `room_defaults`.

Save the downloaded config beside its roster CSV, or adjust `roster_file` under
**Files & advanced settings**. Uploading a CSV checks it in memory; it does not
copy the CSV into your Downloads folder. Generate PDFs with:

```sh
python generate.py --config my_exam.toml --output generated/my_exam
```

**Import config** loads existing TOML or JSON files, including shared defaults,
saved-assignment paths, exclusions, and per-room overrides. Room section choices
can be automatic or selected explicitly. Draft settings are saved in this
browser; uploaded roster contents are kept only in the current session. The
editor serves only its own assets and does not expose local roster files.

When a grid shrinks, blocked seats and aisle boundaries outside it are removed.
If blocking or resizing reduces capacity below the planned count, the editor
reduces that count and displays a notice. Review and recheck before saving.

## Reproduce the approved EEE 212 exam

```sh
python generate.py --config examples/current_exam.toml --output generated/current_exam
```

This example includes the current 81-student roster and the approved room/seat
assignments. It reprints those assignments rather than making a new allocation.
It uses 4 November 2026 at 19:30, 27 students per room, and the frontmost five rows.
The supplied roster and saved assignments contain student information.

## Prepare a new exam

1. Copy `examples/config_template.toml` to a new TOML file (or use the JSON template).
2. Set the exam details, seed, roster filename, and rooms.
3. Provide a CSV with these columns:

   ```csv
   student_id,given_name,surname,section
   00123456,Example,Student,1
   00123457,Another,Student,2
   ```

4. Run:

   ```sh
   python generate.py --config my_exam.toml --output generated/my_exam
   ```

`roster_file` is resolved relative to the config file. You can override it with
`--roster path/to/students.csv`. CLI paths are relative to the current directory.
CSV files must use UTF-8; comma, semicolon, and tab delimiters are accepted.
`section` is optional when mixing sections. The original section-headed `list.csv` format is also
accepted, including its mixed tabs and spaces.

## Configure each room

TOML supports comments. Put repeated settings under `[room_defaults]`, then
list each room with `[[rooms]]`. Keep all exam settings **above** the tables.
Dates and times must be quoted strings.

```toml
course = "EEE 212"
exam_title = "Midterm Exam"
date = "2026-11-04"
time = "19:30"
seed = "EEE212-2026-11-04-v1"
roster_file = "students.csv"

[room_defaults]
rows = 7
columns = 6
student_count = 27
aisles_after = [2, 4] # A/B, C/D, E/F blocks

[[rooms]]
name = "EE03"

[[rooms]]
name = "EE04"
student_count = 26 # Overrides the shared count for this room.
blocked_seats = ["1A"]
```

The precedence is: built-in defaults, exam-level `seating_policy`/`paper_size`,
`room_defaults`, then the individual room. Lists replace inherited lists; use
`aisles_after = []` or `blocked_seats = []` to clear one. `name` belongs to each
room and cannot appear in `room_defaults`. Counts remain explicit so a roster
change cannot silently change a published allocation.

JSON uses the same keys and accepts a `room_defaults` object. Existing fully
specified room objects continue to work. Unknown keys, duplicate settings,
and invalid values fail with an error.

| Setting | Meaning |
| --- | --- |
| `rows` | Number of physical rows, counted from the board. |
| `columns` | Seats per row, labelled A, B, C, etc. |
| `aisles_after` | Insert aisles after these column numbers. `[2,4]` creates A/B, C/D, E/F blocks. `[]` gives one uninterrupted block. |
| `student_count` | Exact number of students allocated to this room. All room counts must sum to the active roster size. |
| `blocked_seats` | Seats unavailable for use, e.g. `["1A","2F"]`. Also supports irregular physical layouts by excluding nonexistent seats. |
| `seating_policy` | `front_first`, `spread`, or `random`. Inherits shared defaults if omitted. |
| `paper_size` | `auto`, `A4`, `A3`, or `A2`. Inherits shared defaults if omitted. |
| `section` | Optional section for this room, such as `"1"`; leave empty for automatic selection. Requires a section policy other than `mixed`. |

Room grids and counts may differ. For example, one room can have 5 rows of
4 seats and 16 students, while another has 8 rows of 6 seats and 30 students.
Add or remove `[[rooms]]` tables to change the number of rooms.

For a small example with two different grids, a blocked seat, and unequal room
counts, run:

```sh
python generate.py --config examples/mixed_rooms.toml --output generated/mixed_rooms
```

`front_first` fills the rows closest to the board before using later rows.
Students in a partially filled row are distributed across the table blocks.
With 27 students and six seats per row, the occupancy is 6, 6, 6, 6, 3.
`spread` balances students across available rows and table blocks.
`random` selects uniformly from all available seats.
All three policies randomize the identities assigned to the selected seats.

`auto` chooses the smallest readable landscape sheet from A4, A3, and A2.
The tool reports each room's selected paper size in `Validation_Report.json`.
It refuses grids that cannot fit readable names and signature spaces on one
of these sheets. Door lists share one A4 portrait PDF. Rooms follow config order,
start on a new page, and have navigation bookmarks. Long lists paginate with repeated room and
column headers and page numbers within each room. Empty rooms retain one page.

## Exam settings

| Setting | Example |
| --- | --- |
| `course` | `"EEE 212"` |
| `exam_title` | `"Midterm Exam"` |
| `date` | `"2026-11-04"` (ISO date) |
| `time` | `"19:30"` (24-hour time) |
| `filename_prefix` | `"EEE212"` (optional; derived from `course`) |
| `seed` | `"EEE212-2026-11-04-v1"` |
| `sort_order` | `"turkish"` or `"unicode"` |
| `section_policy` | `"mixed"` (default), `"separate_rooms"`, or `"keep_together"` |
| `excluded_student_ids` | `[]`, or explicit IDs to omit, such as `["00123456"]` |

Students are identified by ID, not by name. Duplicate IDs, unmatched exclusions,
insufficient room capacity, unavailable seats, and count mismatches stop generation
with an explanation. No additional students are silently dropped.

## Section allocation

| Policy | Behavior |
| --- | --- |
| `mixed` | Shuffle students across all rooms regardless of section. |
| `separate_rooms` | Each room contains only one section. A large section may occupy multiple rooms. |
| `keep_together` | Each section stays entirely in one room, with no other sections in that room. |

Both separate policies require a nonempty `section` for every active student.
The generator respects exact `student_count` values and optional room `section`
choices. It reports an error if these cannot accommodate the sections; it never
mixes sections, drops students, or changes counts silently. The editor's explicit
auto-fit action can change counts to find a feasible arrangement using physical
capacities. A section larger than every room cannot use `keep_together`.

Try the synthetic, course-independent example:

```sh
python generate.py --config examples/sectioned_exam.toml --output generated/sectioned_exam
```

It uses MATH 101, two sections, and three rooms. Import it in the editor to try
both separate policies. Saved assignments are also checked against the selected
section policy when reprinting; changing that policy may require `--fresh`.

## Keep or change an allocation

The same seed, roster, room order, capacities, and seating settings produce the
same allocation. Reordering the CSV does not change it. Set a different seed
when you want a fresh random allocation.

To preserve a published allocation while changing the date, time, or wording:

```sh
python generate.py --config my_exam.toml \
  --reuse-assignments generated/old_exam/EEE212_Assignments.csv \
  --output generated/reprint
```

Alternatively, set `reuse_assignments_file` in the config, relative to that file.
Saved assignments are checked against the current roster and room capacities.
Changed names, missing/extra students, duplicate seats, or newly blocked seats
must be resolved before reprinting.

Use `--fresh` to ignore a configured `reuse_assignments_file` and generate from
the seed instead. The approved example uses saved assignments; the new-exam
template does not.

To replace a previous generated folder, add `--overwrite`. Only folders created
by this generator can be replaced. The previous folder is kept alongside it as
a timestamped backup. A failed generation leaves the previous output in place.

## Outputs

- `EEE212_Seating_Plans.pdf`: one seating/attendance page per room, showing names,
  student IDs, signatures, board position, attendance totals, and **Proctor Name:**.
  Unoccupied seats are blank.
- `EEE212_Door_Lists.pdf`: all rooms' surname-sorted door lists with names, IDs,
  and seat codes in one printable file. There is no list-number column or
  descriptive list heading. The footer explains seat codes and board orientation.
- `EEE212_Assignments.csv`: the master allocation, suitable for Excel and later
  reuse. Student IDs are stored as text in the CSV. To preserve leading zeros
  when importing into Excel, set the Student ID column type to Text.
- `Validation_Report.json`: student totals, room counts and sections, the section
  policy, uniqueness checks, PDF
  text checks, selected paper sizes, and per-room/total door-list page counts.
- `manifest.json`: configuration, roster checksum, seed, and saved assignments.
- `EEE212_Exam_Package.zip`: the generated files together.

The program validates that every active student is assigned exactly once and
every occupied room/seat is unique. It re-imports the generated assignment CSV
and checks each complete name/ID/seat entry exactly once in the correct room
section of each PDF. ZIP contents and integrity are checked before publication.
Generated files and backups stay local under `generated/` and are ignored by Git.

## Tests and maintenance

For development, install [uv](https://docs.astral.sh/uv/) and Node.js 20 or newer,
then use the committed Python lockfile. The UI tests use Node's built-in test
runner; there are no npm dependencies to install.

```sh
uv sync --locked
make check
```

`make check` checks formatting, lint, types, JavaScript syntax/grid behavior, and
the full pytest suite, with a
90% branch-inclusive coverage minimum across the actual generator and seating
code. Checks do not edit source files. Use `make format` to format and fix lint.
On Windows without Make, run the commands directly:

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked python -m pytest
node --check ui/app.js
node --test ui/tests/model.test.js
```

`make init` also installs the pre-commit and conventional commit-message hooks.
GitHub Actions runs tests on Python 3.10, 3.11, and 3.12 for pushes to `main`
and pull requests, and runs lint/types and UI tests on 3.12. `uv run --locked tox` runs the
same supported Python versions locally when those interpreters are installed.

Tests cover both config formats, default inheritance and overrides, input errors,
CSV delimiters and Turkish names, reproducibility, blocked seats, all seat
policies, saved assignments, the approved 81-student allocation, combined PDF
pagination/bookmarks/sorting, CSV and ZIP round trips, CLI options, and recovery
from failed generation or publication. Editor tests cover HTTP endpoints,
config import/export round trips, roster auto-fitting, all three section rules,
explicit room choices, and invalid or conflicting requests. Browser QA also
covers editing seats/aisles, importing files, and downloading a usable config.

PDFs use ReportLab's [canvas API](https://docs.reportlab.com/reportlab/userguide/ch2_graphics/)
and [embedded TrueType fonts](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/).
The bundled Noto Sans fonts support the Turkish characters in this roster.
Their redistribution license is in `fonts/OFL.txt`; font upstream:
<https://github.com/notofonts/noto-fonts>.
