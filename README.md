# Sanay3i (صنايعي)

**AI-powered automation for mechanical CAD — describe a part in Arabic or
English, and it's drawn.**

A user describes a part in plain language (or speaks it), an LLM translates
that into structured JSON commands, and Sanay3i executes them in SolidWorks
through its COM API.

## Ownership & License

**This project is proprietary software.**
Copyright © 2026 **Eng. Osama Isa Ali Alassar** (المهندس أسامة عيسى علي العصار).
**All Rights Reserved.**

Eng. Osama Isa Ali Alassar is the sole author, designer, and owner of
Sanay3i (صنايعي) and all of its components. This repository is shared **for
review only** — access grants no license or right to use, copy, modify, or
redistribute any part of it. See [LICENSE](LICENSE) and [NOTICE](NOTICE)
for the full terms and the declaration of authorship.

Third-party trademarks — including SOLIDWORKS, a trademark of Dassault
Systemes — belong to their respective owners; Sanay3i is an independent
work, not affiliated with or endorsed by them.

**Current scope (v1.1)**: the whole pipeline, now **voice-driven too**. You
can *speak* a part/assembly description — Arabic or English — and it flows
through the exact same path as typing it. A **natural-language layer**
turns a plain-language description into the same structured JSON the engine
already validates, expands, and executes. It is deliberately model-agnostic:
the primary **copy-paste mode** generates a self-contained prompt bundle you
paste into *any* free AI chat (no API key), then pastes the reply back for
validation; an optional **API mode** calls any OpenAI-compatible endpoint
directly. Underneath sits the
JSON-command engine built over v0.1–v0.5: parts, assemblies, dimensioned 2D
drawings, and a **curves engine** for true swept/curved geometry — real
involute spur and internal ring gears, ISO-606 sprockets, a generic revolve,
and cosmetic helical threads. A pure-math curve layer generates the exact
gear/sprocket invariants the digital twin verifies *and* the spline profile
the COM backend draws; curved features that can't be reduced to a box
degrade gracefully to a bounding envelope. Everything except the final COM
hop — including the LLM validate/repair loop — runs and is tested without
SolidWorks, in CI, in the cloud, anywhere. See [ROADMAP.md](ROADMAP.md).

```
"a gear m2 z20…"  ──▶  swpilot ai  ──▶  prompt bundle ──▶ any free AI chat
                                                                │  (JSON)
                                              swpilot ai-apply ─┘
                                                     │ validate + one repair
                                                     ▼
                                              commands.json ──▶ swpilot run
                                                                │
                                              ┌─────────────────┴──────────────┐
                                              ▼                                ▼
                                      --backend mock                --backend solidworks
                                    (any OS, CI-safe)             (Windows + SolidWorks)
```

## Plain language → a part (v1.0)

No API key needed — works with any free AI chat:

```bash
swpilot ai "a 100x50x10 plate with 4 corner holes" --out prompt.txt
# paste prompt.txt into any AI chat, save its reply as reply.txt
swpilot ai-apply reply.txt          # extracts + validates + runs (mock)
```

Arabic works too — `swpilot ai "بدي ترس m2 بـ20 سن مع تجويف 16 وخابور"`
yields a valid `involute_spur_gear` command file. If the AI's JSON doesn't
validate, `ai-apply` prints a ready-to-paste repair prompt built from the
real validator errors instead of executing anything. With an
OpenAI-compatible endpoint configured (`SWPILOT_LLM_MODEL`,
`SWPILOT_LLM_BASE_URL`, `SWPILOT_LLM_API_KEY`), `swpilot ai "…" --mode api`
does the round-trip (with one auto-repair) for you.

## Speak it instead (v1.1)

`swpilot voice` is a thin front-end over the exact pipeline above — it only
adds capture + transcription, then a light dialect normalization, and hands
the text to `swpilot ai`. It never bypasses validation or the pre-execution
confirmation.

```bash
# Already have a transcript (or typing it)? Normalize + run it:
swpilot voice --text "بدي ترس موديول اثنين عشرين سن مع تجويف ستاشر وخابور"

# Have an audio clip but no transcription key? Offline handoff:
swpilot voice clip.wav          # saves/keeps the audio + prints how to transcribe
#   → transcribe with any free tool, then: swpilot voice --text "<transcript>"

# With a Whisper-compatible endpoint configured, it transcribes for you:
export SWPILOT_STT_API_KEY=…    # + optional SWPILOT_STT_BASE_URL / _MODEL (whisper-1)
swpilot voice clip.wav --mode api
swpilot voice --mode api        # record from the mic (needs: pip install 'swpilot[voice]')
```

The **dialect-normalization** step maps spoken number words and units to
canonical forms before the LLM — `عشرين`→`20`, `ستاشر`→`16`, `twenty five`→`25`,
`ملم`→`mm` — and is careful *not* to merge two distinct numbers: `موديول اثنين
عشرين سن` becomes `موديول 2 20 سن` (module 2, 20 teeth), never `22`. Actual
microphone capture and real speech-to-text accuracy can only be verified on
hardware; CI proves the file-path, transcription-plumbing, and normalization
paths.

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
│   ├── curves.py            #   parametric curve generators (involute, ISO-606, revolve, helix)
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

### Curves engine (v0.5)

True swept/curved geometry the prismatic engine can't make. New curve
primitives — `draw_spline`, `draw_arc`, `draw_line`, `revolve`,
`helix_thread` — plus four macros:

| op | key fields | notes |
|---|---|---|
| `involute_spur_gear` | `module`, `teeth`, `pressure_angle` (20), `face_width`, `bore`, `hub_*?`, `keyway?` | real involute flank + tangent root fillet + tip land, patterned z times on a root cylinder |
| `internal_ring_gear` | `module`, `teeth`, `face_width`, `rim_outer_diameter` | involute teeth cut inward into a rim |
| `sprocket_iso` | `chain` (`08B`…`16B`), `teeth`, `face_width`, `bore`, `keyway?` | ISO-606 roller-seating + flank profile |
| `revolve` (primitive) | `axis`, `angle` (360), `reverse` | solid of revolution of the active sketch; upgrades catalog approximations (v-pulley, belleville) to true geometry |
| `helix_thread` (primitive) | `diameter`, `pitch`, `length`, `right_handed` | **cosmetic** swept thread rib, never load-bearing |
| `gear_mesh_check` | `a`, `b`, `expected_center_distance?` | pure validation: two gears mesh iff equal module + pressure angle; center distance a = m·(z₁+z₂)/2 |

The enabling design is a **pure-math curve layer** (`swpilot/model/curves.py`)
that emits both the exact invariants and the profile the backend draws.
Standard metric gear math (pitch Ø = m·z, base = pitch·cos α, addendum m,
dedendum 1.25 m). The involute unwinds by roll angle t:
`x = rb(cos t + t·sin t)`, `y = rb(sin t − t·cos t)`.

**The twin degrades gracefully.** It can't reduce a spline-flanked tooth to
a box, so a curved feature carries a bounding annulus/cylinder (so a gear
stays a valid assembly component with a resolvable envelope for mates) plus
the computed invariants. It **verifies exactly in CI**: pitch/base/tip/root
diameters, tooth count, tooth thickness (πm/2), the undercut flag
(z < 2/sin²α), bore-fits-under-root, and the mesh check. It **delegates to
Windows** (numbered in the WINDOWS_SETUP v0.5 checklist): spline fit
fidelity, that mirror+pattern yields a closed extrudable profile, the exact
root trochoid, and revolve/helix solid validity. Nothing is a silent pass —
anything the twin can't verify is a warning that names what Windows must
confirm.

Acceptance: `examples/spur_gear_m2_z20.json` (module-2, 20-tooth, 20° gear,
Ø16 bore + keyway), `examples/gear_mesh_check.json` (that gear meshing-
checked against a 40-tooth mate at 60 mm center distance), and
`examples/v_pulley_revolved.json` (the v-pulley rebuilt with `revolve`).

## Run reports

Every `swpilot run` writes `<file>.report.json`: per-command status
(`ok`/`error`/`skipped` — execution is fail-fast), warnings, the full COM call
log with arguments (in meters, as SolidWorks receives them), and a final model
state snapshot. On the mock backend that snapshot is the simulated feature
tree; on the real backend it is read back from SolidWorks.

## What v0.5 deliberately does not do

Native Hole Wizard features (holes are composed cuts by design), sketch
constraints/dimensions as commands, width mates, non-English SolidWorks
installs (planes are selected by their English names), cross-family cut
containment validation (warning only), footprint-union computation
(warning only), rotated-rectangle pattern instances in twin validation
(warning only); on drawings: non-front sections, offset/stepped section
lines, detail views, GD&T; on curves: profile-shifted (corrected) gears,
helical/bevel/worm gears, the exact hob-generated root trochoid (a tangent
arc stands in), load-bearing thread forms (the helix is cosmetic), and
twin-side containment of spline cuts (envelope + Windows-verified). The
LLM layer comes last; its integration point is frozen: it produces a
`CommandFile` JSON document, and everything downstream already exists.
