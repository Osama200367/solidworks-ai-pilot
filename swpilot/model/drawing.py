"""Drawing twin: sheet layout, view placement, and sheet-space mapping.

A :class:`DrawingTracker` is a session document like parts and
assemblies. It validates every drawing command before any COM call:
views are placed on a computed grid (third- or first-angle), checked
against the sheet's content area and each other, and every dimension
attachment is a model point projected through the view into exact sheet
coordinates for ``SelectByID2`` — the same closed-form-pick discipline
the part and assembly twins use.

View image conventions (identical in third and first angle — the
projection standard changes only view PLACEMENT, never an individual
view's image):

* front view: (u, v) = (x, y)
* top view: (x, -z) — above the front in third angle, below in first
* right view: (-z, y) — right of the front in third angle, left in first

Sections are always placed past the right edge (vertical cutting line)
or past the top edge (horizontal line) of the existing views, in both
projections. Their image DOES depend on the projection standard,
because SolidWorks orients the section arrows to match the sheet's
projection angle (a first-angle section placed right is viewed from the
left). That arrow-orientation rule is an assumption only a Windows
smoke test can confirm (WINDOWS_SETUP.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from swpilot.model.assembly import AssemblyTracker
from swpilot.model.tracker import ModelError, ModelTracker
from swpilot.tolerances import EPS

Vec3 = tuple[float, float, float]
Point2 = tuple[float, float]

# Landscape (width, height) in mm.
SHEET_SIZES: dict[str, tuple[float, float]] = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}
SHEET_MARGIN = 10.0
# Bottom strip reserved for the sheet format's title block.
TITLE_BLOCK_HEIGHT = 40.0
# Gap between view cells: room for dimensions between views.
VIEW_GAP = 30.0
# Dimension band reserved around each view.
DIM_BAND = 14.0
# Note stack reserved under the front view (first line starts 10 mm
# into the band; 4 lines fit).
NOTES_BAND = 30.0
NOTE_LINE = 6.0
# Standard drawing scale series, largest first.
STANDARD_SCALES: list[tuple[int, int]] = [
    (10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100),
]


# --------------------------------------------------------------------------
# Backend-facing specs (pure data; built by the twin, executed by backends)
# --------------------------------------------------------------------------


@dataclass
class DrawingSetup:
    """Everything the backend needs for ``create_drawing``."""

    name: str
    model_doc: str
    model_path: str
    sheet: str
    paper_w: float  # mm
    paper_h: float  # mm
    scale: tuple[int, int]
    first_angle: bool
    properties: list[tuple[str, str]]  # custom properties set on the model
    units_note_text: str
    units_note_position: Point2  # sheet mm


@dataclass
class ViewSpec:
    """One drawing view for the backend to create."""

    name: str
    kind: str  # front | top | right | iso
    method: str  # "model" (CreateDrawViewFromModelView3) | "projected"
    position: Point2  # sheet mm, view center
    orientation: str | None = None  # "*Front", "*Isometric", ... (model method)
    model_path: str | None = None  # model method
    parent: str | None = None  # projected method
    scale: float | None = None  # explicit view scale (iso), else sheet scale


@dataclass
class SectionSpec:
    """A full section view: cutting line + placement."""

    name: str
    parent: str
    label: str  # "A" -> the view reads A-A
    line: tuple[float, float, float, float]  # x1, y1, x2, y2 sheet mm
    position: Point2  # sheet mm, section view center


@dataclass
class DimSpec:
    """One smart dimension: picks and placement in sheet mm."""

    name: str
    view: str
    kind: str  # "linear" | "diameter"
    picks: list[Point2]  # SelectByID2 coordinates on view geometry
    placement: Point2  # dimension text position
    value: float  # mm — what the dimension should read
    prefix: str | None = None  # e.g. "4X " (SolidWorks <MOD-DIAM> tokens allowed)
    below: str | None = None  # callout line below (counterbore data etc.)

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "name": self.name,
            "view": self.view,
            "kind": self.kind,
            "value": self.value,
        }
        if self.prefix:
            d["prefix"] = self.prefix
        if self.below:
            d["below"] = self.below
        return d


@dataclass
class NoteSpec:
    """A plain sheet note."""

    text: str
    position: Point2  # sheet mm


# --------------------------------------------------------------------------
# Twin-side view records
# --------------------------------------------------------------------------


@dataclass
class ViewRec:
    name: str
    kind: str  # front | top | right | iso | section
    center: Point2  # sheet mm
    size: Point2  # scaled image extents, sheet mm
    scale: float
    orientation: str | None = None  # sections: "vertical" | "horizontal"

    def cell(self, extra_below: float = 0.0) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the view plus its dimension band."""
        w2, h2 = self.size[0] / 2.0 + DIM_BAND, self.size[1] / 2.0 + DIM_BAND
        return (
            self.center[0] - w2,
            self.center[1] - h2 - extra_below,
            self.center[0] + w2,
            self.center[1] + h2,
        )


def _model_aabb(model: ModelTracker | AssemblyTracker, op: str) -> tuple[Vec3, Vec3]:
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    if isinstance(model, ModelTracker):
        # solid_features() covers prismatic bosses, curved bosses, AND
        # curved circular patterns (a whole gear's tip-diameter disk) — a
        # boss-only loop would miss the patterned teeth and draw one tooth.
        for name in model.solid_features():
            lo, hi = model.feature_aabb(name)
            for i in range(3):
                mins[i] = min(mins[i], lo[i])
                maxs[i] = max(maxs[i], hi[i])
    else:
        for comp in model.components.values():
            lo, hi = comp.world_aabb()
            for i in range(3):
                mins[i] = min(mins[i], lo[i])
                maxs[i] = max(maxs[i], hi[i])
    if mins[0] == float("inf"):
        raise ModelError(f"{op}: the referenced document has no solid geometry to draw")
    return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


class DrawingTracker:
    """Twin state for one drawing document."""

    def __init__(
        self,
        name: str,
        model_doc: str,
        model: ModelTracker | AssemblyTracker,
        model_path: str,
        sheet: str,
        scale: tuple[int, int] | None,
        projection: str,
        title: str | None,
        drawn_by: str,
        date: str,
    ) -> None:
        self.name = name
        self.model_doc = model_doc
        self.model = model
        self.model_path = model_path
        self.sheet = sheet
        self.sheet_w, self.sheet_h = SHEET_SIZES[sheet]
        self.projection = projection
        self.aabb = _model_aabb(model, "create_drawing")
        self.views: dict[str, ViewRec] = {}
        self.view_order: list[str] = []
        self.dimensions: list[DimSpec] = []
        self.notes: list[NoteSpec] = []
        self.saved_to: list[str] = []
        self._warnings: list[str] = []
        self._section_n = 0

        if scale is not None:
            self.scale_ratio = scale
        else:
            self.scale_ratio = self._auto_scale()
        self.properties: list[tuple[str, str]] = [
            ("Description", title if title is not None else model_doc),
            ("DrawnBy", drawn_by),
        ]
        if date:
            self.properties.append(("DrawnDate", date))
        marker = getattr(model, "saved_feature_marker", None)
        if marker is not None and marker != self._model_change_marker():
            self._warn(
                f"create_drawing: document {model_doc!r} changed after its last save; "
                "the drawing references the saved file — re-save the model first"
            )

    # -- warnings ------------------------------------------------------

    def _warn(self, message: str) -> None:
        self._warnings.append(message)

    def pop_warnings(self) -> list[str]:
        out, self._warnings = self._warnings, []
        return out

    # -- geometry helpers ----------------------------------------------

    def _model_change_marker(self) -> int:
        if isinstance(self.model, ModelTracker):
            return len(self.model.features)
        return len(self.model.components) + len(self.model.mates)

    @property
    def extents(self) -> Vec3:
        lo, hi = self.aabb
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    @property
    def model_center(self) -> Vec3:
        lo, hi = self.aabb
        return ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, (lo[2] + hi[2]) / 2.0)

    @property
    def scale_factor(self) -> float:
        return self.scale_ratio[0] / self.scale_ratio[1]

    def _image_extents(self, kind: str, orientation: str | None = None) -> Point2:
        """Unscaled (w, h) of a view image in model mm."""
        dx, dy, dz = self.extents
        if kind == "front":
            return (dx, dy)
        if kind == "top" or (kind == "section" and orientation == "horizontal"):
            return (dx, dz)
        if kind == "right" or (kind == "section" and orientation == "vertical"):
            return (dz, dy)
        assert kind == "iso"
        # Isometric bounding box under the standard 30-degree axonometry.
        return (0.866 * (dx + dz), dy + 0.5 * (dx + dz))

    def _content(self) -> tuple[float, float, float, float]:
        """Usable sheet area (x0, y0, x1, y1): margins + title-block strip."""
        return (
            SHEET_MARGIN,
            SHEET_MARGIN + TITLE_BLOCK_HEIGHT,
            self.sheet_w - SHEET_MARGIN,
            self.sheet_h - SHEET_MARGIN,
        )

    def _iso_scale(self, explicit: tuple[int, int] | None) -> tuple[int, int]:
        if explicit is not None:
            return explicit
        try:
            i = STANDARD_SCALES.index(self.scale_ratio)
        except ValueError:
            return self.scale_ratio  # non-standard sheet scale: reuse it
        return STANDARD_SCALES[min(i + 1, len(STANDARD_SCALES) - 1)]

    def _ortho_offsets(self, views: list[str], s: float) -> dict[str, Point2]:
        """View-center offsets relative to the front view, per projection."""
        wf, hf = self._image_extents("front")
        wt, ht = self._image_extents("top")
        wr, hr = self._image_extents("right")
        sign = 1.0 if self.projection == "third" else -1.0
        out: dict[str, Point2] = {"front": (0.0, 0.0)}
        if "top" in views:
            gap = VIEW_GAP
            if self.projection == "first":
                # The top view sits BELOW the front in first angle, where
                # the front's note band also lives — leave room for it.
                gap += NOTES_BAND
            out["top"] = (0.0, sign * ((hf + ht) / 2.0 * s + gap))
        if "right" in views:
            out["right"] = (sign * ((wf + wr) / 2.0 * s + VIEW_GAP), 0.0)
        return out

    def _group_fits(self, views: list[str], scale: tuple[int, int], with_iso: bool) -> bool:
        s = scale[0] / scale[1]
        offsets = self._ortho_offsets(views, s)
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for name, (ox, oy) in offsets.items():
            w, h = self._image_extents(name)
            below = NOTES_BAND if name == "front" else 0.0
            x0 = min(x0, ox - w / 2.0 * s - DIM_BAND)
            y0 = min(y0, oy - h / 2.0 * s - DIM_BAND - below)
            x1 = max(x1, ox + w / 2.0 * s + DIM_BAND)
            y1 = max(y1, oy + h / 2.0 * s + DIM_BAND)
        cx0, cy0, cx1, cy1 = self._content()
        gw, gh = x1 - x0, y1 - y0
        if gw > cx1 - cx0 or gh > cy1 - cy0:
            return False
        if with_iso:
            iso = self._iso_scale(None)
            iw, ih = self._image_extents("iso")
            iw, ih = iw * iso[0] / iso[1] + 2 * DIM_BAND, ih * iso[0] / iso[1] + 2 * DIM_BAND
            if gw + iw + 12.0 > cx1 - cx0 and gh + ih + 12.0 > cy1 - cy0:
                return False
        return True

    def _auto_scale(self) -> tuple[int, int]:
        all_views = ["front", "top", "right"]
        for candidate in STANDARD_SCALES:
            self.scale_ratio = candidate  # _iso_scale reads it
            if self._group_fits(all_views, candidate, with_iso=True):
                return candidate
        raise ModelError(
            f"create_drawing: the model ({self.extents[0]:.0f} x {self.extents[1]:.0f}"
            f" x {self.extents[2]:.0f} mm) does not fit an {self.sheet} sheet at any "
            "standard scale"
        )

    # -- placement / registration --------------------------------------

    def _register_view(self, rec: ViewRec) -> None:
        self._check_placement(rec)
        self.views[rec.name] = rec
        self.view_order.append(rec.name)

    def _check_placement(self, rec: ViewRec) -> None:
        extra = NOTES_BAND if rec.kind == "front" else 0.0
        x0, y0, x1, y1 = rec.cell(extra_below=extra)
        cx0, cy0, cx1, cy1 = self._content()
        if x0 < cx0 - EPS or y0 < cy0 - EPS or x1 > cx1 + EPS or y1 > cy1 + EPS:
            num, den = self.scale_ratio
            raise ModelError(
                f"view {rec.name!r} does not fit the {self.sheet} sheet at scale "
                f"{num}:{den} (cell [{x0:.0f}, {y0:.0f}]..[{x1:.0f}, {y1:.0f}] mm vs "
                f"content [{cx0:.0f}, {cy0:.0f}]..[{cx1:.0f}, {cy1:.0f}] mm); use a "
                "smaller scale or a larger sheet"
            )
        for other in self.views.values():
            ox0, oy0, ox1, oy1 = other.cell(
                extra_below=NOTES_BAND if other.kind == "front" else 0.0
            )
            if x0 < ox1 - EPS and ox0 < x1 - EPS and y0 < oy1 - EPS and oy0 < y1 - EPS:
                raise ModelError(
                    f"view {rec.name!r} overlaps view {other.name!r}; use a smaller "
                    "scale, a larger sheet, or (for isometric views) another corner"
                )

    def _require_no_model_drift(self, op: str) -> None:
        marker = getattr(self.model, "saved_feature_marker", None)
        if marker is not None and marker != self._model_change_marker():
            self._warn(
                f"{op}: document {self.model_doc!r} changed after its last save; the "
                "drawing shows the saved file, not the current twin state"
            )

    def standard_views(self, views: list[str]) -> list[ViewSpec]:
        for v in views:
            if v in self.views:
                raise ModelError(f"standard_views: view {v!r} already exists on this sheet")
        self._require_no_model_drift("standard_views")
        s = self.scale_factor
        offsets = self._ortho_offsets(views, s)
        # Anchor the whole group at the bottom-left of the content area.
        x0 = y0 = float("inf")
        for name, (ox, oy) in offsets.items():
            w, h = self._image_extents(name)
            below = NOTES_BAND if name == "front" else 0.0
            x0 = min(x0, ox - w / 2.0 * s - DIM_BAND)
            y0 = min(y0, oy - h / 2.0 * s - DIM_BAND - below)
        cx0, cy0, _, _ = self._content()
        ax, ay = cx0 - x0, cy0 - y0
        specs: list[ViewSpec] = []
        order = [v for v in ("front", "top", "right") if v in offsets]
        for name in order:
            ox, oy = offsets[name]
            w, h = self._image_extents(name)
            rec = ViewRec(
                name=name,
                kind=name,
                center=(ox + ax, oy + ay),
                size=(w * s, h * s),
                scale=s,
            )
            self._register_view(rec)
            if name == "front":
                specs.append(
                    ViewSpec(
                        name=name,
                        kind=name,
                        method="model",
                        orientation="*Front",
                        model_path=self.model_path,
                        position=rec.center,
                    )
                )
            else:
                specs.append(
                    ViewSpec(
                        name=name,
                        kind=name,
                        method="projected",
                        parent="front",
                        position=rec.center,
                    )
                )
        return specs

    def isometric_view(
        self, corner: str, scale: tuple[int, int] | None
    ) -> ViewSpec:
        if "iso" in self.views:
            raise ModelError("isometric_view: this sheet already has an isometric view")
        self._require_no_model_drift("isometric_view")
        num, den = self._iso_scale(scale)
        s = num / den
        w, h = self._image_extents("iso")
        w, h = w * s, h * s
        cx0, cy0, cx1, cy1 = self._content()
        x = cx1 - DIM_BAND - w / 2.0 if corner.endswith("right") else cx0 + DIM_BAND + w / 2.0
        y = cy1 - DIM_BAND - h / 2.0 if corner.startswith("top") else cy0 + DIM_BAND + h / 2.0
        rec = ViewRec(name="iso", kind="iso", center=(x, y), size=(w, h), scale=s)
        self._register_view(rec)
        return ViewSpec(
            name="iso",
            kind="iso",
            method="model",
            orientation="*Isometric",
            model_path=self.model_path,
            position=rec.center,
            scale=s if (num, den) != self.scale_ratio else None,
        )

    def section_view(self, parent: str, orientation: str) -> SectionSpec:
        p = self.views.get(parent)
        if p is None:
            raise ModelError(
                f"section_view: parent view {parent!r} does not exist yet; place it "
                "with standard_views first"
            )
        if p.kind != "front":
            raise ModelError(
                "section_view: v0.4 sections cut the front view only (the parent "
                "must be 'front')"
            )
        self._require_no_model_drift("section_view")
        label = chr(ord("A") + self._section_n)
        s = self.scale_factor
        w, h = self._image_extents("section", orientation)
        w, h = w * s, h * s
        # Sections go past the right edge (vertical line) or top edge
        # (horizontal line) of everything already placed, in both
        # projections; SolidWorks orients the arrows per the sheet's
        # projection angle.
        if orientation == "vertical":
            x = max(v.cell()[2] for v in self.views.values()) + 8.0 + DIM_BAND + w / 2.0
            y = p.center[1]
        else:
            x = p.center[0]
            y = max(v.cell()[3] for v in self.views.values()) + 8.0 + DIM_BAND + h / 2.0
        name = f"section_{label}"
        rec = ViewRec(
            name=name,
            kind="section",
            center=(x, y),
            size=(w, h),
            scale=s,
            orientation=orientation,
        )
        self._register_view(rec)
        self._section_n += 1
        margin = 4.0
        if orientation == "vertical":
            sheet_line = (
                p.center[0],
                p.center[1] - p.size[1] / 2.0 - margin,
                p.center[0],
                p.center[1] + p.size[1] / 2.0 + margin,
            )
        else:
            sheet_line = (
                p.center[0] - p.size[0] / 2.0 - margin,
                p.center[1],
                p.center[0] + p.size[0] / 2.0 + margin,
                p.center[1],
            )
        # The cutting line is sketched with the parent view ACTIVATED, so
        # its coordinates are parent-view sketch space: model-scale mm
        # relative to the view's sketch origin (the projection of the
        # model origin), NOT absolute sheet space — the official
        # CreateSectionViewAt5 example sketches its line at negative
        # coordinates while the placement point stays in sheet range.
        ox, oy = self.sheet_point(parent, (0.0, 0.0, 0.0))
        line = (
            (sheet_line[0] - ox) / p.scale,
            (sheet_line[1] - oy) / p.scale,
            (sheet_line[2] - ox) / p.scale,
            (sheet_line[3] - oy) / p.scale,
        )
        return SectionSpec(
            name=name, parent=parent, label=label, line=line, position=rec.center
        )

    # -- dimension support ---------------------------------------------

    def project(self, view: ViewRec, p: Vec3) -> Point2:
        """View-image coordinates (unscaled model mm) of a world point.

        First- vs third-angle changes only where views are PLACED, never
        an individual view's image: the top view is (x, -z) and the right
        view (-z, y) under both conventions (the unfolding hinge and the
        flipped plane cancel).
        """
        x, y, z = p
        first = self.projection == "first"
        k = view.kind
        if k in ("front", "iso"):
            return (x, y)
        if k == "top":
            return (x, -z)
        if k == "right":
            return (-z, y)
        assert k == "section"
        # Sections are placed right (vertical line) / above (horizontal).
        # Assumption pending Windows verification: SolidWorks orients the
        # section arrows per the sheet's projection angle, so a first-
        # angle section placed right is viewed from the LEFT (+z image)
        # and one placed above is viewed from BELOW (+z image).
        if view.orientation == "vertical":
            return (z, y) if first else (-z, y)
        return (x, z) if first else (x, -z)

    def sheet_point(self, view_name: str, p: Vec3) -> Point2:
        """Sheet-mm position of a model point's projection in a view."""
        v = self.views[view_name]
        u, w = self.project(v, p)
        uc, wc = self.project(v, self.model_center)
        return (v.center[0] + (u - uc) * v.scale, v.center[1] + (w - wc) * v.scale)

    def smart_dimensions(self) -> tuple[list[DimSpec], list[NoteSpec]]:
        from swpilot.model import dimensioning

        if not self.views:
            raise ModelError(
                "smart_dimensions: the drawing has no views yet; place views first"
            )
        self._require_no_model_drift("smart_dimensions")
        dims, notes, warnings = dimensioning.analyze(self)
        for w in warnings:
            self._warn(w)
        self.dimensions.extend(dims)
        self.notes.extend(notes)
        return dims, notes

    # -- save / summary ------------------------------------------------

    def save_drawing(self, path: str) -> None:
        if not self.views:
            self._warn("save_drawing: the drawing has no views")
        self.saved_to.append(path)

    def summary(self) -> dict[str, object]:
        num, den = self.scale_ratio
        return {
            "document": self.name,
            "kind": "drawing",
            "of": self.model_doc,
            "sheet": self.sheet,
            "scale": f"{num}:{den}",
            "projection": self.projection,
            "views": [
                {
                    "name": v.name,
                    "kind": v.kind,
                    "position": [round(v.center[0], 3), round(v.center[1], 3)],
                    "scale": round(v.scale, 6),
                }
                for v in (self.views[n] for n in self.view_order)
            ],
            "dimensions": [d.to_dict() for d in self.dimensions],
            "notes": [n.text for n in self.notes],
            "saved_to": list(self.saved_to),
        }
