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

### v0.3 — Assemblies ◀ current phase

Multi-part sessions in one flat command stream (named documents, active-
document routing): insert components from same-run parts or external files
(declared envelopes), position them with translations + 90°-step rotations,
and constrain them with coincident/concentric/distance/parallel mates. The
twin gains an axis-aligned snap-solver: mates pin coordinates exactly,
over-constraint (including mismatched hole patterns) fails at validate
time, redundancy and under-constraint warn, and the bolt_circle macro
builds fastener sets straight from hole features. Acceptance case: a
manufacturable base + cover + M8 SHCS bolt circle saved as .SLDASM.

### v0.4 — Engineering drawings

Drawing documents from parts/assemblies: sheet + template selection,
standard and projected views, section and detail views, dimensions,
annotations, title-block fields. Command families for view placement and
dimensioning; mock validation checks view/reference existence and sheet
bounds.

### v1.0 — LLM natural-language layer

Natural language → `CommandFile` JSON, on top of the full vocabulary of
v0.1–v0.4. `swpilot/llm/` fills in: prompt/tool schemas derived from the
pydantic models, iterative repair using validator and simulator errors as
feedback (the same errors CI uses), and a conversational CLI. Everything
downstream — validation, expansion, simulation, execution, reporting —
already exists and is identical for LLM-authored and hand-written files.
