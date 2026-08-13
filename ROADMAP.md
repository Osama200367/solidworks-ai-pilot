# SW-Pilot Roadmap

The end state: a user describes what they want in plain language, an LLM
translates it into SW-Pilot's structured JSON commands, and the system
executes them in SolidWorks — with everything except the final COM hop
developed, validated, and regression-tested without SolidWorks installed.

## The architectural through-line

Two v0.1 decisions carry every later phase:

- **The layered command vocabulary.** Each phase adds new *command families*
  (new pydantic models in the discriminated union, new macro expansions, new
  `CallSpec` builders, new mock validation rules). The executor, the report
  format, the CLI, and the isolation boundary do not change.
- **The backend ABC + shared call plan.** A new command family means new
  methods on the `Backend` contract, implemented twice against the same
  `CallSpec` objects: once logging/validating (mock, runs in CI), once
  executing (COM, runs on Windows). CI-validated call logs remain exactly
  what executes against SolidWorks.

The LLM layer (v1.0) is deliberately last: every phase before it enlarges the
JSON vocabulary the LLM will emit, and none of them depend on it.

## Phases

### v0.1 — Parts foundation ✅ (merged)

JSON command files drive part creation end to end: primitives (`new_part`,
`create_sketch` on standard planes, `draw_rectangle`, `draw_circle`,
`extrude`, `cut_extrude`, `save_part`) plus `create_plate` /
`add_corner_holes` macros. Stateful mock simulator (geometry validation,
zero-thickness detection, material-footprint checks), pywin32 COM backend,
fail-fast executor with attributed run reports, `run`/`validate`/`expand`
CLI, CI with enforced COM isolation, 112 tests.

### v0.2 — Richer part modeling ✅ (merged)

New command families: fillets and chamfers; counterbore/countersink holes
(Hole Wizard-style results built pragmatically from composed cuts, with
nominal metric fastener presets); linear and circular feature patterns
(real SolidWorks pattern features over auto-created reference axes); slots;
sketching on planar faces and offset reference planes. The enabling design —
how JSON refers to specific edges and faces without COM handles — is
declarative selectors resolved by a shared geometric model tracker (the
"digital twin") into coordinate-based selection, with the twin promoted out
of the mock so it validates every backend, including the real one, before
any COM call.

### v0.3 — Assemblies ✅ (merged)

Multi-part sessions in one flat command stream (named documents, active-
document routing): insert components from same-run parts or external files
(declared envelopes), position them with translations + 90°-step rotations,
and constrain them with coincident/concentric/distance/parallel mates. The
twin gains an axis-aligned snap-solver: mates pin coordinates exactly,
over-constraint (including mismatched hole patterns) fails at validate
time, redundancy and under-constraint warn, and the bolt_circle macro
builds fastener sets straight from hole features. Acceptance case: a
manufacturable base + cover + M8 SHCS bolt circle saved as .SLDASM.

### v0.4 — Engineering drawings ✅ (merged)

Dimensioned 2D sheets (.SLDDRW) from parts and assemblies — the final
pre-LLM milestone. `create_drawing` (A4/A3, standard scale series with
auto-pick, third- or first-angle projection, title-block metadata via
custom properties), `standard_views` (front anchor + projected top/right),
`isometric_view`, `section_view` (center cutting lines, labeled A-A, B-B —
the hollow-turned-part view), and `smart_dimensions`: not a dump, but the
governing set — envelope (W/H/T or diameters + length), N×Ø hole callouts
with datum position dims, pattern pitch, fillet/chamfer/slot note block.
The drawing twin lays views out on a validated sheet grid and projects
every dimension attachment into exact sheet coordinates for selection.
Acceptance cases: the v0.2 bracket sheet and a hollow flange with a
section view proving internal bore dimensioning.

### v0.5 — The curves engine ◀ current phase

True swept and curved geometry the prismatic engine can't make.
`involute_spur_gear` (the flagship: a real parametric involute flank +
tangent root fillet + tip land, patterned z times on a root cylinder,
with bore/hub/keyway), `internal_ring_gear` (involute teeth cut inward),
`sprocket_iso` (ISO-606 roller-seating + flank profile), `revolve` (a
generic solid of revolution — cones, grooves, radii, and the upgrade path
for approximated catalog parts like the v-pulley), and a cosmetic
`helix_thread`. The enabling design is a pure-math curve layer
(`curves.py`) that emits both the exact invariants the digital twin
verifies (pitch/base/tip/root diameters, tooth thickness, undercut, mesh)
and the spline/arc/line profile the COM backend draws. The twin can't box
a spline tooth, so a curved feature degrades gracefully to a bounding
annulus/cylinder — validating all the engineering math and the envelope
in CI while delegating spline fidelity and solid validity to Windows.
Acceptance cases: a module-2 20-tooth gear meshing-checked against a
module-2 40-tooth mate, and the v-pulley rebuilt with revolve to prove the
approximation→real upgrade.

### v1.0 — LLM natural-language layer

Natural language → `CommandFile` JSON, on top of the full vocabulary of
v0.1–v0.5. `swpilot/llm/` fills in: prompt/tool schemas derived from the
pydantic models, iterative repair using validator and simulator errors as
feedback (the same errors CI uses), and a conversational CLI. Everything
downstream — validation, expansion, simulation, execution, reporting —
already exists and is identical for LLM-authored and hand-written files.
