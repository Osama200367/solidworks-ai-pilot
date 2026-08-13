# SW-Pilot

AI-powered automation layer for SolidWorks. A user describes a part in plain
language, an LLM translates that into structured JSON commands, and SW-Pilot
executes them in SolidWorks through its COM API.

**Current scope (v0.4)**: the JSON-command half of that pipeline, through
**engineering drawings** — one command file builds parts and assemblies,
then produces dimensioned 2D sheets (`.SLDDRW`): standard/projected views,
isometric and section views, and *smart dimensioning* (the governing
features a machinist needs, never an auto-dimension dump), with the title
block filled from metadata. Everything except the final COM hop runs and
is tested without SolidWorks — in CI, in the cloud, anywhere. See
[ROADMAP.md](ROADMAP.md) for the phase plan; the LLM translation layer
comes later and will simply emit the same JSON.

```
natural language ──▶ [LLM layer, v0.2] ──▶ commands.json ──▶ swpilot run
                                                                │
                                              ┌─────────────────┴──────────────┐
                                              ▼                                ▼
                                      --backend mock                --backend solidworks
                                    (any OS, CI-safe)             (Windows + SolidWorks)
```

## Quick start (no SolidWorks needed)

```bash
pip install -e ".[dev]"
swpilot run examples/plate_with_holes.json            # mock backend by default
swpilot validate examples/plate_with_holes.json       # schema + macro check only
swpilot expand examples/plate_with_holes.json         # show macro expansion
pytest                                                # full suite, no Windows
```

On Windows with SolidWorks installed (see [WINDOWS_SETUP.md](WINDOWS_SETUP.md)):

```powershell
pip install -e ".[windows]"
swpilot run examples\plate_with_holes.json --backend solidworks
```

## The example that defines v0.1

`examples/plate_with_holes.json` — a 100×50×10 mm plate with four 8 mm corner
holes:

```json
{
  "schema_version": "0.1",
  "commands": [
    { "op": "create_plate", "width": 100, "height": 50, "thickness": 10 },
    { "op": "add_corner_holes", "diameter": 8, "margin": 10 },
    { "op": "save_part", "path": "plate_100x50.SLDPRT" }
  ]
}
```

`examples/plate_primitives.json` builds the identical part from low-level
primitives — the two layers of the same command vocabulary.

## Architecture

```
swpilot/
├── commands/                # pure Python, no COM anywhere
│   ├── schema.py            #   pydantic models, discriminated on "op"
│   ├── macros.py            #   macro → primitive expansion (twin-backed)
│   └── loader.py            #   JSON → validated commands → expansion + twin pass
├── model/                   # the shared "digital twin"
│   ├── planes.py            #   plane frames: sketch-space ↔ world-space
│   ├── geometry.py          #   2D predicates (rect/circle/slot)
│   ├── tracker.py           #   ModelTracker: state, validation, edge derivation
│   ├── assembly.py          #   AssemblyTracker: components, mates, snap-solver
│   ├── drawing.py           #   DrawingTracker: sheet layout, views, sheet-space picks
│   ├── dimensioning.py      #   smart-dimension analyzer (governing features only)
│   ├── session.py           #   named documents + active-document routing
│   ├── apply.py             #   command → tracker dispatch (used twice, see below)
│   └── presets.py           #   metric fastener hole presets (M3–M12)
├── backends/
│   ├── base.py              #   Backend ABC: one method per primitive
│   ├── calls.py             #   shared COM call plan (CallSpec builders)
│   ├── mock/                #   thin call logger — CI's backend
│   └── solidworks/          #   pywin32 COM backend — Windows only, lazy import
├── executor.py              # tracker-validated dispatch, builds run report
├── cli.py                   # swpilot run / validate / expand
└── llm/                     # v1.0: natural language → command JSON
```

Design rules that keep the cloud/CI side honest:

- **Isolation boundary.** `pywin32` lives in the `[windows]` extra; nothing
  outside `swpilot/backends/solidworks/` imports it, the CLI imports that
  package only when `--backend solidworks` is selected, and
  `tests/test_isolation.py` enforces this in CI on every platform.
- **One call plan, two backends.** `backends/calls.py` builds `CallSpec`
  objects describing each COM invocation (target, method, args in meters).
  The mock backend logs them; the COM backend logs **and executes the same
  objects**. The call log CI asserts on is therefore exactly what runs on
  Windows — the two cannot drift.
- **One twin, every backend.** The executor runs a `ModelTracker` for every
  backend: it validates each command (cuts outside material, tangent
  contours/zero-thickness geometry, empty sketches, oversized fillets,
  pattern instances escaping material) and resolves declarative selectors
  before any backend method is called. On Windows this means an invalid
  file fails *before* the first COM call. The loader runs the same twin
  during macro expansion, so `swpilot validate` catches geometric errors
  with no backend at all.
- **Selection without COM handles.** Edges are selected declaratively
  (`{"select": "vertical_corners"}`, `{"near_point": [x,y,z]}`); the twin
  derives real 3D edge positions and emits coordinate-based `SelectByID2`
  calls. Faces for sketching resolve to auto-created offset reference planes
  (deterministic sketch axes). The COM backend renames every created feature
  to the twin's name, so name references (`of_feature`, pattern seeds) are
  exact on both sides.
- **Macros expand before execution.** `create_plate` becomes
  `new_part → create_sketch → draw_rectangle → extrude` in the loader, so
  macro logic is tested with zero backend involvement, and the run report
  attributes every primitive back to the command the user wrote.

## Command reference (schema 0.1)

All lengths in **millimeters**; sketch coordinates are 2D on the sketch plane,
origin at the model origin. NaN/infinite values are rejected. Unknown ops and
unknown fields are rejected.

### Primitives

| op | fields | notes |
|---|---|---|
| `new_part` | — | one part per run |
| `create_plane` | `name`, `offset_from` (plane name), `distance` (signed) | offset reference plane |
| `create_axis` | `axis`: `x`\|`y`\|`z` | world axis through the origin (auto-inserted by patterns) |
| `create_sketch` | `plane` (any plane name, default `front`) *or* `on` `{facing, of_feature?}` | face refs resolve to offset planes |
| `draw_rectangle` | `center` `[x,y]` (default origin), `width`, `height` | center rectangle |
| `draw_circle` | `center` `[x,y]` (default origin), `diameter` | |
| `draw_slot` | `start` `[x,y]`, `end` `[x,y]`, `width` | straight slot, center-to-center |
| `extrude` | `depth`, `reverse` (default false) | blind boss; consumes the active sketch |
| `cut_extrude` | `through_all` (default true) *or* `depth`; `reverse`; `draft_angle` (blind only) | consumes the active sketch |
| `fillet` | `radius`, `edges` selector | constant radius |
| `chamfer` | `distance`, `angle` (default 45), `edges` selector | distance-angle |
| `linear_pattern` | `features` (names), `direction` (`±x/±y/±z`), `spacing`, `count`, `direction2?` | real SW pattern feature |
| `circular_pattern` | `features`, `axis` (`x/y/z`), `count`, `total_angle` (default 360), `equal_spacing` | about a world axis |
| `save_part` | `path` (must end `.SLDPRT`) | |

Edge selectors: `{"select": "vertical_corners" | "top_loop" | "bottom_loop" |
"all", "of_feature": "Boss-Extrude1"}` (feature defaults to the most recent
one with matching edges), or the escape hatch `{"near_point": [x, y, z]}` —
the unconsumed edge nearest a world point.

### Macros

| op | fields | expands to |
|---|---|---|
| `create_plate` | `width`, `height`, `thickness`, `plane` (default `front`) | new_part + sketch + rectangle + extrude |
| `add_corner_holes` | `diameter`, `margin` | sketch + 4 circles + through-all cut in the last rectangular boss |
| `hole` | `at` (positions), `type`: `simple`\|`counterbore`\|`countersink`, `diameter`, `cb_*`/`cs_*`, `standard?`, `on?` | offset plane (auto) + composed cuts; countersink cone = drafted blind cut |

`hole` drills from the top face of the last boss by default (`on` accepts a
plane name or a face reference). `"standard": "M6"` fills unset dimensions
from a nominal metric table (ISO 273 clearance, socket-head counterbores,
90° flat-head countersinks, M3–M12) — nominal conveniences, not certified
standard data; explicit fields always win.

### Assemblies (v0.3)

| op | fields | notes |
|---|---|---|
| `new_part` / `new_assembly` | `name?` | named documents in one flat stream; creating activates |
| `activate_document` | `name` | explicit switching |
| `insert_component` | `part` *or* `file`+`envelope?`, `name?`, `at`, `rotate` (90° steps), `fixed?` | same-run parts must be `save_part`-ed first; first component auto-fixed |
| `mate` | `type`, `a`, `b`, `value?` | entities: `{component, facing, of_feature?}` (planar) or `{component, of_feature, at?}` (cylindrical); `width` deferred |
| `save_assembly` | `path` (`.SLDASM`) | reports under-constrained components |
| `bolt_circle` (macro) | `bolt {part, shank_feature, head_feature}`, `holes {component, of_feature}`, `seat` | one bolt per hole: insert + concentric + head-seat coincident, positions read from the twin |

The **axis-aligned snap-solver** makes mates on this geometry exact: mates
pin translation axes and lock rotations, components snap into solved
positions, conflicting pins fail as over-constrained (mismatched hole
patterns are caught with the offset in mm), duplicate pins warn as
redundant, and `save_assembly` reports free DOF per component — with a
bolt's spin about its own mated axis labeled as normal for fasteners.
`examples/bolted_cover.json` is the acceptance case: a 120×80×12 base, an
8 mm cover located by one coincident + two concentric mates, and four
in-run-modeled M8 socket-head cap screws placed by `bolt_circle` through
Ø9 clearance holes.

### Drawings (v0.4)

| op | fields | notes |
|---|---|---|
| `create_drawing` | `name?`, `of?` (default: active doc), `sheet` `A4`\|`A3`, `scale?` `[num,den]`, `projection` `third`\|`first`, `title?`, `drawn_by`, `date?` | the referenced document must be saved first; omitted scale auto-picks the largest standard scale that fits |
| `standard_views` | `views` (default `[front, top, right]`, must include `front`) | front placed as a model view, top/right projected from it — SolidWorks applies the sheet's projection angle |
| `isometric_view` | `corner` (default `top_right`), `scale?` | defaults to one scale-series step smaller than the sheet |
| `section_view` | `parent` (default `front`), `orientation` `vertical`\|`horizontal` | full section through the model center, auto-labeled A-A, B-B, …; the hollow-turned-part view |
| `smart_dimensions` | — | see below |
| `save_drawing` | `path` (`.SLDDRW`) | |

**Smart dimensioning** emits the governing set, not a dump: overall
envelope (W/H/T for rectangular parts; outer Øs + length for
silhouette-detected turned parts, with bore and lengths in the section
view when one exists), hole callouts in `N×Ø` form (counterbore/
countersink data on a second line) with position dimensions from the
datum edges, linear-pattern pitch, a bolt-circle note for circular
patterns, and a note block (fillets, chamfers, slots) under the front
view. Anything the analyzer cannot place safely is skipped **with a
warning** — never silently. The drawing twin validates view placement
against the sheet's content area (scale/fit errors are actionable:
"use a smaller scale or a larger sheet") and projects every dimension
attachment into exact sheet coordinates for selection.

Title-block fields (description/title, drawn-by, date) are set as custom
properties on the model, which SolidWorks' standard sheet formats display
via `$PRPSHEET` links; sheet scale and size are auto-linked by SolidWorks.
A `DIMENSIONS IN MM` note is placed explicitly. Acceptance cases:
`examples/bracket_drawing.json` (the v0.2 bracket on an A3 sheet) and
`examples/flange_drawing.json` (hollow flange, A4, front + section A-A
proving internal bore dimensioning + isometric).

Expansion rejects holes that would cross an edge, overlap each other, or
touch anything tangentially (SolidWorks refuses zero-thickness geometry) —
and because expansion runs the twin, *every* geometric rule fails at
`swpilot validate` time, before any backend runs.

## Run reports

Every `swpilot run` writes `<file>.report.json`: per-command status
(`ok`/`error`/`skipped` — execution is fail-fast), warnings, the full COM call
log with arguments (in meters, as SolidWorks receives them), and a final model
state snapshot. On the mock backend that snapshot is the simulated feature
tree; on the real backend it is read back from SolidWorks.

## What v0.4 deliberately does not do

Native Hole Wizard features (holes are composed cuts by design), sketch
constraints/dimensions as commands, width mates, non-English SolidWorks
installs (planes are selected by their English names), cross-family cut
containment validation (warning only), footprint-union computation
(warning only), rotated-rectangle pattern instances in twin validation
(warning only); on drawings: sections of views other than the front view,
offset/stepped section lines, detail views, GD&T, per-slot dimensions
(slots travel in the note block), dimension-text collision avoidance
beyond band reservation, and assembly sheets beyond views + envelope
dims (component features belong on their own part sheets). The LLM layer
comes last; its integration point is frozen: it produces a `CommandFile`
JSON document, and everything downstream already exists.
