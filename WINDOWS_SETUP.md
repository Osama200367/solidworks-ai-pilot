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
2. **Negative-offset reference planes** — `create_plane` with a negative
   distance uses `swRefPlaneReferenceConstraint_OptionFlip` (256); verify
   the plane lands on the correct side of its base.
3. **Feature renaming** — after a run, the feature tree names should match
   the run report exactly (`Boss-Extrude1`, `Cut-Extrude1`, `SWPilot_Plane1`,
   `SWPilot_Axis_Z`, ...).
4. **Countersink cone** — check the drafted cut produces the expected major
   diameter at the surface (the draft direction flag `Ddir1` is the thing to
   flip if the cone opens the wrong way).

## v0.3 smoke-test checklist (assemblies)

Run `examples/bolted_cover.json` and verify, in order:

1. **Document juggling** — four documents open (base, cover, m8_bolt,
   assembly); `ActivateDoc2` addresses documents by their real window
   titles, which the backend captures at creation.
2. **AddMate5** — the highest-risk call of the phase (15 parameters,
   align=CLOSEST; the ByRef ErrorStatus slot is filled with a VT_BYREF
   VARIANT at execution time, and tuple results from early-bound
   dispatch are unwrapped). Components are inserted with a small
   standoff and the mate selections use PRE-solve coordinates — AddMate5
   itself performs the closing move; the cover should land flush on the
   base with the hole patterns aligned.
3. **Component renaming via `Name2`** — instance names in the tree must
   read base_1, cover_1, bolt_1..bolt_4.
4. **Coordinate face picks inside components** — mate selections use world
   coordinates of transformed faces; if a mate grabs a wrong face, compare
   the pick coordinates in the report's `resolved` entities against the
   model.
5. **Component rotation** — none is needed in the acceptance file; test a
   `rotate: [{"axis": "z", "degrees": 90}]` insert of a visibly
   asymmetric part separately to verify the `Transform2` /
   `IMathUtility.CreateTransform` path (a 180° rotation is symmetric and
   cannot reveal a transposed matrix; ArrayData rows are the images of
   the local axes).
6. **External components** — inserting a `file:` component first issues
   `OpenDoc6` (ByRef Errors/Warnings as VARIANTs) and re-activates the
   assembly before `AddComponent5`.

## v0.4 smoke-test checklist (drawings)

The drawing COM surface is the riskiest SW-Pilot uses. CI proves the twin
math and that the mock/COM call plans are identical; the following can
**only** be verified live. Run `examples/flange_drawing.json` first (it
exercises everything), then `examples/bracket_drawing.json`.

1. **Section view sequence** — the single riskiest sequence of the
   project: `ActivateView("front")` → `SketchManager.CreateLine` with
   the cutting line in PARENT-VIEW sketch coordinates (model scale,
   origin = the projection of the model origin — matching the official
   `CreateSectionViewAt5` example) → `CreateSectionViewAt5(x, y, 0,
   "A", 0, Nothing, 0)` (placement in sheet meters) consuming the
   still-selected line → `ActivateSheet(<live sheet name>)`. The live
   check is the view-coordinate SCALE convention: if the flange's
   cutting line lands on the part but at half/double length, adjust the
   view-scale division in `DrawingTracker.section_view`. The sheet name
   is read live from `GetCurrentSheet` and a failed `ActivateSheet`
   aborts the run (a silent failure would leave later annotation picks
   in view coordinates).
2. **Section image orientation** — the twin assumes SolidWorks orients
   the section arrows to the sheet's projection angle: a vertical section
   placed right shows the right-view image (`u = -z` third-angle,
   `u = +z` first-angle). If the bore/length dimensions in the flange's
   section attach mirrored, flip the section case in
   `DrawingTracker.project`.
3. **Sheet-space dimension picks** — `SelectByID2("", "EDGE", x, y, 0)`
   at computed sheet coordinates must snap to the intended view edge.
   Verify the flange bore Ø30 (picks on the two internal profile lines)
   and the bracket 120 envelope width (picks on the left/right outline).
   The twin assumes a drawing view's position is its geometry center
   (the projected AABB center); a constant offset on every pick means
   that assumption is wrong for your SolidWorks version.
4. **AddDimension2 inference** — one circle pick must yield a diameter
   dimension; two parallel edges a linear dimension; an edge + a circle
   a to-center linear dimension. Verify the counterbore callout renders
   `4X Ø6.6` with the `⌴Ø11 ↧6` second line (the `<HOLE-SPOT>`/
   `<HOLE-DEPTH>`/`<MOD-DIAM>` tokens must render as symbols, not
   literal text) via `IDisplayDimension.SetText`.
5. **Paper/template constants** — `NewDocument(template, 6|8, 0, 0)`
   (A4/A3 landscape from `swDwgPaperSizes_e`) and
   `Sheet.SetProperties(paper, template, num, den, firstAngle, w, h)`.
   If the sheet comes up the wrong size or loses its format, the
   `SW_PAPER_SIZES` / `SW_SHEET_TEMPLATES` values in
   `swpilot/backends/calls.py` are the first suspects.
6. **Projected view placement** — `CreateUnfoldedViewAt3` drops the top
   view above and the right view to the right (third angle). Also run a
   `"projection": "first"` variant: the placements flip (top below,
   right to the left) while each view's IMAGE stays identical to the
   third-angle one — first vs third angle never changes an individual
   view's image, only the arrangement.
7. **Title block** — the custom properties (Description, DrawnBy,
   DrawnDate) set via `CustomPropertyManager("").Add3` (delete-and-add,
   so re-drawing the same model updates them) on the *model* must appear
   in the sheet format's `$PRPSHEET` fields; a customized template may
   not map every field (the `DIMENSIONS IN MM` note is placed explicitly
   and must always appear bottom-left).
8. **View renaming via `SetName2`** — the views must be named `front`,
   `top`, `right`, `iso`, `section_A` in the tree; projected-view
   selection (`SelectByID2(..., "DRAWINGVIEW", ...)`) depends on it.

## What runs where

| layer | cloud/CI | Windows |
|---|---|---|
| schema, macros, loader | ✅ tested | same code |
| mock simulator + call log | ✅ tested | available (`--backend mock`) |
| COM backend | compile/typecheck only | executes the identical call plan |
