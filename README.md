# SW-Pilot

AI-powered automation layer for SolidWorks. A user describes a part in plain
language, an LLM translates that into structured JSON commands, and SW-Pilot
executes them in SolidWorks through its COM API.

**v0.1 scope**: the JSON-command half of that pipeline. A command file drives
SolidWorks (new part → sketch → extrude → holes → save), and everything except
the final COM hop runs and is tested without SolidWorks — in CI, in the cloud,
anywhere. The LLM translation layer comes next and will simply emit the same
JSON.

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
│   ├── macros.py            #   create_plate / add_corner_holes → primitives
│   └── loader.py            #   JSON → validated commands → macro expansion
├── backends/
│   ├── base.py              #   Backend ABC: one method per primitive
│   ├── calls.py             #   shared COM call plan (CallSpec builders)
│   ├── mock/                #   stateful simulator — what CI tests
│   └── solidworks/          #   pywin32 COM backend — Windows only, lazy import
├── executor.py              # walks commands, dispatches, builds run report
├── cli.py                   # swpilot run / validate / expand
└── llm/                     # v0.2: natural language → command JSON
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
- **The mock is a simulator, not a stub.** It tracks sketches, features, and
  material footprints, and rejects what SolidWorks would reject: cuts outside
  material, overlapping/tangent contours in one sketch (zero-thickness
  geometry), extruding an empty sketch, cutting before any solid exists.
  Passing the mock means the command *sequence and geometry* are sound, not
  just parseable.
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
| `create_sketch` | `plane`: `front`\|`top`\|`right` (default `front`) | standard reference planes only |
| `draw_rectangle` | `center` `[x,y]` (default origin), `width`, `height` | center rectangle |
| `draw_circle` | `center` `[x,y]` (default origin), `diameter` | |
| `extrude` | `depth` | blind boss; consumes the active sketch |
| `cut_extrude` | `through_all` (default true) *or* `depth` | consumes the active sketch |
| `save_part` | `path` (must end `.SLDPRT`) | |

### Macros

| op | fields | expands to |
|---|---|---|
| `create_plate` | `width`, `height`, `thickness`, `plane` (default `front`) | new_part + sketch + rectangle + extrude |
| `add_corner_holes` | `diameter`, `margin` | sketch + 4 circles + through-all cut, placed inside the last `create_plate` envelope |

`margin` is the distance from each pair of edges to the hole center. Expansion
rejects holes that would cross an edge, overlap each other, or touch anything
tangentially (SolidWorks refuses zero-thickness geometry), so bad geometry
fails at `swpilot validate` time — before any backend runs.

## Run reports

Every `swpilot run` writes `<file>.report.json`: per-command status
(`ok`/`error`/`skipped` — execution is fail-fast), warnings, the full COM call
log with arguments (in meters, as SolidWorks receives them), and a final model
state snapshot. On the mock backend that snapshot is the simulated feature
tree; on the real backend it is read back from SolidWorks.

## What v0.1 deliberately does not do

Hole Wizard features, sketch constraints/dimensions as commands, assemblies,
drawings, multiple parts per run, non-English SolidWorks installs (reference
planes are selected by their English names), cross-plane cut containment
validation, footprint-union computation (a cut spanning the seam of several
merged same-plane bosses produces a warning, not a verdict), and the LLM
layer itself. The integration point for the LLM is
frozen, though: it produces a `CommandFile` JSON document, and everything
downstream already exists.
