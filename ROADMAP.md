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

### v0.2 — Richer part modeling ◀ current phase

New command families: fillets and chamfers; counterbore/countersink holes
(Hole Wizard-style results built pragmatically from composed cuts); linear
and circular feature patterns; slots; sketching on arbitrary planar faces
and offset reference planes. The enabling design problem — how JSON refers
to specific edges and faces without COM handles — is solved by declarative
selectors resolved against a shared geometric model tracker (see the v0.2
design discussion in the PR for this phase).

### v0.3 — Assemblies

Multi-part sessions: insert components from saved parts, position them, and
constrain them with mates (coincident, concentric, distance, angle …).
Component/mate command families; the model tracker grows an assembly tree;
mock validation catches over-constraint and impossible mates cheaply.

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
