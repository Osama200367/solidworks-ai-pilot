# Running SW-Pilot against real SolidWorks (Windows)

The `solidworks` backend was written against the documented SolidWorks
2022–2025 COM API but is developed in the cloud, where it cannot be executed.
Treat your first run as a smoke test: everything up to the COM boundary is
covered by CI, and the run report's call log shows exactly which COM call
failed if one does.

## Prerequisites

- Windows 10/11 with SolidWorks 2022–2025 installed (a licensed seat that can
  open interactively; English-language installation — reference planes are
  selected by the names "Front Plane", "Top Plane", "Right Plane")
- Python 3.11+ (64-bit, matching SolidWorks' bitness)

## Install

```powershell
git clone <this repo>
cd solidworks-ai-pilot
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[windows]"
```

## First run

1. Start SolidWorks manually once and make sure default document templates are
   configured (Tools → Options → System Options → Default Templates). SW-Pilot
   uses the default part template; if yours is unset, pass
   `--template "C:\ProgramData\SolidWorks\SOLIDWORKS 20xx\templates\Part.prtdot"`
   or set the `SWPILOT_PART_TEMPLATE` environment variable.
2. Run the acceptance example:

```powershell
swpilot run examples\plate_with_holes.json --backend solidworks
```

SolidWorks will launch (or be reused if already running), build the plate with
its four holes, save `plate_100x50.SLDPRT` into the current directory, and
write `examples\plate_with_holes.json.report.json`.

Useful flags: `--no-visible` runs SolidWorks without showing the window;
`--report out.json` chooses the report location.

## Troubleshooting

- **`could not connect to SolidWorks via COM`** — SolidWorks isn't installed,
  or the COM registration is broken. Repair the SolidWorks installation, or
  run it once manually as the same user.
- **`SolidWorks returned no default part template`** — see step 1 above.
- **A specific COM call fails** — the error message names the call (e.g.
  `Model.FeatureManager.FeatureCut3`) and the report's `call_log` contains the
  exact arguments sent. Compare with the same command file under
  `--backend mock` (whose log must be identical up to the failing call) and
  file an issue with both reports.
- **Dialogs blocking automation** — close open documents and dismiss modal
  dialogs; COM calls silently fail or hang while a modal dialog is up.
- **Version notes** — the COM calls used (`FeatureExtrusion2`, `FeatureCut3`,
  `FeatureFillet3`, `InsertFeatureChamfer`, `FeatureLinearPattern4`,
  `FeatureCircularPattern4`, `InsertRefPlane`, `InsertAxis2`,
  `CreateSketchSlot`, `SelectByID2`, `CreateCenterRectangle`,
  `CreateCircleByRadius`, `SaveAs3`) exist across SolidWorks 2022–2025.
  SW-Pilot deliberately avoids version-specific type libraries
  (`EnsureDispatch`); it uses late-bound dynamic dispatch with hardcoded
  constants so one install works against any supported SolidWorks version.

## v0.2 smoke-test checklist (things designed for verification)

1. **Edge pick coordinates** — run `examples/bracket.json`: the fillet must
   land on the four plate corners. If it selects wrong edges, the sketch-axis
   conventions in `swpilot/model/planes.py` need adjusting (they are pinned
   by `tests/test_planes.py`).
2. **Negative-offset reference planes** — `SW_REF_PLANE_OPTION_FLIP` in
   `swpilot/backends/calls.py` is the least-certain constant in the
   codebase; verify a `create_plane` with negative distance lands on the
   correct side.
3. **Feature renaming** — after a run, the feature tree names should match
   the run report exactly (`Boss-Extrude1`, `Cut-Extrude1`, `SWPilot_Plane1`,
   `SWPilot_Axis_Z`, ...).
4. **Countersink cone** — check the drafted cut produces the expected major
   diameter at the surface (the draft direction flag `Ddir1` is the thing to
   flip if the cone opens the wrong way).

## What runs where

| layer | cloud/CI | Windows |
|---|---|---|
| schema, macros, loader | ✅ tested | same code |
| mock simulator + call log | ✅ tested | available (`--backend mock`) |
| COM backend | compile/typecheck only | executes the identical call plan |
