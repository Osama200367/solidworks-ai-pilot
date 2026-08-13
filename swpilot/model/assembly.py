"""Assembly twin: components, mates, and the axis-aligned snap-solver.

Because every part is axis-aligned and every component rotation is a
90-degree step, mates on this geometry are exactly solvable by simple
coordinate pinning:

* coincident/distance (planar faces) pin the component's translation
  along the face-normal axis and lock the two rotations about the
  perpendicular axes;
* concentric (cylindrical faces) pins the two translations perpendicular
  to the cylinder axis; a second concentric about a distinct parallel
  axis locks the remaining rotation;
* parallel locks rotations only (with 90-degree orientations it must
  already hold, or the mate is impossible).

Pinning the same axis twice with the same value is a redundant mate
(warning); with a different value it is over-constrained (error) — which
is also how mismatched hole patterns between two plates are caught. At
save time, non-fixed components with unpinned axes are reported as
under-constrained (warning).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swpilot.model import geometry as g
from swpilot.model.planes import Vec3
from swpilot.model.tracker import ModelError, ModelTracker
from swpilot.model.transforms import Transform
from swpilot.tolerances import EPS

_AXIS_NAMES = ("x", "y", "z")


@dataclass
class ResolvedFace:
    """A planar component face: world normal axis + plane position."""

    component: str
    axis: int  # 0/1/2 — the world axis the normal lies on
    normal_sign: float  # +1 / -1 along that axis (outward)
    position: float  # world coordinate of the plane along `axis`, mm
    pick: Vec3  # world pick point on the face, mm

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "kind": "face",
            "normal": f"{'+' if self.normal_sign > 0 else '-'}{_AXIS_NAMES[self.axis]}",
            "position": self.position,
            "pick": list(self.pick),
        }


@dataclass
class ResolvedCylinder:
    """A cylindrical component face: world axis + axis line + radius."""

    component: str
    axis: int  # world axis the cylinder runs along
    center: Vec3  # a world point on the cylinder axis, mm
    radius: float
    pick: Vec3  # world pick point on the wall, mm
    feature_kind: str = "cut"  # "boss" (shank) or "cut" (hole)

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "kind": "cylinder",
            "axis": _AXIS_NAMES[self.axis],
            "center": list(self.center),
            "radius": self.radius,
            "pick": list(self.pick),
        }


ResolvedEntity = ResolvedFace | ResolvedCylinder


@dataclass
class ComponentRec:
    name: str
    source: str  # part document name, or an external file path
    part: ModelTracker | None  # same-run part twin (None for external)
    envelope: tuple[float, float, float] | None  # external: declared w/h/t
    transform: Transform
    fixed: bool = False
    saved_path: str | None = None  # the file the COM backend inserts
    pinned: dict[int, float] = field(default_factory=dict)  # axis -> t value
    locked_rot: set[int] = field(default_factory=set)
    concentric_anchors: list[tuple[int, Vec3]] = field(default_factory=list)

    def free_translations(self) -> list[str]:
        return [_AXIS_NAMES[a] for a in range(3) if a not in self.pinned]

    def free_rotations(self) -> list[str]:
        return [_AXIS_NAMES[a] for a in range(3) if a not in self.locked_rot]

    def local_aabb(self) -> tuple[Vec3, Vec3]:
        if self.part is not None:
            mins = [float("inf")] * 3
            maxs = [float("-inf")] * 3
            for f in self.part.features:
                if f.kind != "boss":
                    continue
                lo, hi = self.part.feature_aabb(f.name)
                for i in range(3):
                    mins[i] = min(mins[i], lo[i])
                    maxs[i] = max(maxs[i], hi[i])
            if mins[0] == float("inf"):
                raise ModelError(
                    f"component {self.name!r}: its part has no solid geometry to mate"
                )
            return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])
        assert self.envelope is not None
        w, h, t = self.envelope
        return (-w / 2.0, -h / 2.0, 0.0), (w / 2.0, h / 2.0, t)

    def world_aabb(self) -> tuple[Vec3, Vec3]:
        lo, hi = self.local_aabb()
        corners = [
            self.transform.apply((x, y, z))
            for x in (lo[0], hi[0])
            for y in (lo[1], hi[1])
            for z in (lo[2], hi[2])
        ]
        mins = tuple(min(c[i] for c in corners) for i in range(3))
        maxs = tuple(max(c[i] for c in corners) for i in range(3))
        return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


@dataclass
class MateRec:
    name: str
    mate_type: str
    a: ResolvedEntity
    b: ResolvedEntity
    value: float | None = None


class AssemblyTracker:
    """One assembly document: components, mates, solved transforms."""

    kind = "assembly"

    def __init__(self, name: str) -> None:
        self.name = name
        self.components: dict[str, ComponentRec] = {}
        self.mates: list[MateRec] = []
        self.saved_to: list[str] = []
        self._warnings: list[str] = []
        self._mate_n = 0
        self._instance_counters: dict[str, int] = {}

    # -- warnings ------------------------------------------------------

    def _warn(self, message: str) -> None:
        self._warnings.append(message)

    def pop_warnings(self) -> list[str]:
        out, self._warnings = self._warnings, []
        return out

    # -- components ----------------------------------------------------

    def next_instance_name(self, source: str) -> str:
        self._instance_counters[source] = self._instance_counters.get(source, 0) + 1
        return f"{source}_{self._instance_counters[source]}"

    def component(self, name: str) -> ComponentRec:
        try:
            return self.components[name]
        except KeyError:
            raise ModelError(
                f"unknown component {name!r}; existing: {sorted(self.components)}"
            ) from None

    def insert_component(
        self,
        name: str,
        source: str,
        part: ModelTracker | None,
        envelope: tuple[float, float, float] | None,
        transform: Transform,
        fixed: bool,
        saved_path: str | None,
    ) -> ComponentRec:
        if name in self.components:
            raise ModelError(f"insert_component: component name {name!r} already exists")
        if part is not None and saved_path is None:
            raise ModelError(
                f"insert_component: part {source!r} has not been saved; SolidWorks "
                "inserts components from files, so add a save_part command to that "
                "part before inserting it"
            )
        if part is None and envelope is None:
            self._warn(
                f"insert_component: external component {name!r} has no declared "
                "envelope; it can be placed but its faces cannot be resolved for "
                "mates"
            )
        first = not self.components
        rec = ComponentRec(
            name=name,
            source=source,
            part=part,
            envelope=envelope,
            transform=transform,
            fixed=fixed or first,
            saved_path=saved_path,
        )
        if first and not fixed:
            self._warn(
                f"insert_component: first component {name!r} is automatically fixed "
                "(SolidWorks convention)"
            )
        if rec.fixed:
            rec.pinned = {i: transform.translation[i] for i in range(3)}
            rec.locked_rot = {0, 1, 2}
        self.components[name] = rec
        return rec

    # -- entity resolution ---------------------------------------------

    def resolve_face(
        self, component: str, facing: str, of_feature: str | None
    ) -> ResolvedFace:
        comp = self.component(component)
        axis = _AXIS_NAMES.index(facing[1])
        sign = 1.0 if facing[0] == "+" else -1.0
        world_dir: Vec3 = tuple(sign if i == axis else 0.0 for i in range(3))  # type: ignore[assignment]

        if comp.part is None:
            if comp.envelope is None:
                raise ModelError(
                    f"mate: external component {component!r} has no declared envelope; "
                    "faces cannot be resolved (declare one on insert_component)"
                )
            self._warn(
                f"mate: face of external component {component!r} resolved from its "
                "DECLARED envelope; the twin cannot verify the actual file geometry"
            )
            lo, hi = comp.world_aabb()
            pos = hi[axis] if sign > 0 else lo[axis]
            mid = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
            mid[axis] = pos
            return ResolvedFace(component, axis, sign, pos, (mid[0], mid[1], mid[2]))

        # Same-run part: resolve in LOCAL frame with the existing v0.2
        # machinery, then map through the component transform.
        local_dir = comp.transform.rotate_back(world_dir)
        local_facing = _dir_to_facing(local_dir)
        if of_feature is not None:
            feature = comp.part.feature(of_feature)
            if feature.kind != "boss":
                raise ModelError(
                    f"mate: face references need a boss feature, "
                    f"{of_feature!r} of {component!r} is a {feature.kind}"
                )
            lo_l, hi_l = comp.part.feature_aabb(of_feature)
        else:
            lo_l, hi_l = _part_aabb(comp)
        l_axis = _AXIS_NAMES.index(local_facing[1])
        l_sign = 1.0 if local_facing[0] == "+" else -1.0
        l_pos = hi_l[l_axis] if l_sign > 0 else lo_l[l_axis]
        pick_local = self._face_pick_local(
            comp, l_axis, l_sign, l_pos, lo_l, hi_l, owner=of_feature
        )
        pick = comp.transform.apply(pick_local)
        return ResolvedFace(component, axis, sign, pick[axis], pick)

    def _face_pick_local(
        self,
        comp: ComponentRec,
        l_axis: int,
        l_sign: float,
        l_pos: float,
        lo: Vec3,
        hi: Vec3,
        owner: str | None,
    ) -> Vec3:
        """A local point on the face, dodging regions where the face is not.

        Two blocker kinds: material *removed* by through-cuts (holes), and
        material *added* by other bosses whose extrusion touches this face
        plane (a bolt shank growing out of the head's seat face — picking
        there would grab an ambiguous or wrong face).
        """
        assert comp.part is not None
        others = [i for i in range(3) if i != l_axis]
        cu = (lo[others[0]] + hi[others[0]]) / 2.0
        cv = (lo[others[1]] + hi[others[1]]) / 2.0
        spans = (hi[others[0]] - lo[others[0]], hi[others[1]] - lo[others[1]])
        candidates = [(cu, cv)]
        for du, dv in ((-0.3, -0.3), (0.3, -0.3), (-0.3, 0.3), (0.3, 0.3)):
            candidates.append((cu + du * spans[0], cv + dv * spans[1]))

        def local_point(u: float, v: float) -> Vec3:
            p = [0.0, 0.0, 0.0]
            p[l_axis] = l_pos
            p[others[0]] = u
            p[others[1]] = v
            return (p[0], p[1], p[2])

        blockers = _face_blockers(comp.part, l_axis, l_sign, l_pos, owner)
        material = _face_material(comp.part, l_axis, l_sign, l_pos, owner)
        for u, v in candidates:
            pt = local_point(u, v)
            blocked = any(
                g.covers(shape, g.Circle(pu, pv, 0.2))
                for shape, (pu, pv) in (
                    (s, _project_uv(comp.part, fam, pt)) for s, fam in blockers
                )
            )
            on_material = any(
                g.covers(shape, g.Circle(pu, pv, 0.2))
                for shape, (pu, pv) in (
                    (s, _project_uv(comp.part, fam, pt)) for s, fam in material
                )
            )
            if not blocked and on_material:
                return pt
        self._warn(
            "mate: face pick point may fall inside a hole or under an adjoining "
            "boss; verify the selection on the first Windows run"
        )
        return local_point(*candidates[0])

    def resolve_cylinder(
        self, component: str, of_feature: str, at: tuple[float, float] | None
    ) -> ResolvedCylinder:
        comp = self.component(component)
        if comp.part is None:
            raise ModelError(
                f"mate: cylinder selectors need a same-run part; component "
                f"{component!r} is external"
            )
        feature = comp.part.feature(of_feature)
        if feature.sketch is None or feature.kind not in ("boss", "cut"):
            raise ModelError(
                f"mate: {of_feature!r} of {component!r} has no cylindrical geometry"
            )
        circles = [e for e in feature.sketch.entities if isinstance(e, g.Circle)]
        if not circles:
            raise ModelError(
                f"mate: {of_feature!r} of {component!r} contains no circles; "
                "cylindrical faces come from circular bosses/cuts"
            )
        if at is None:
            if len(circles) > 1:
                raise ModelError(
                    f"mate: {of_feature!r} of {component!r} has {len(circles)} "
                    "circles; disambiguate with 'at': [u, v] (sketch coordinates)"
                )
            circle = circles[0]
        else:
            circle = min(circles, key=lambda c: (c.cx - at[0]) ** 2 + (c.cy - at[1]) ** 2)
        frame = feature.sketch.frame
        if feature.kind == "cut":
            span = comp.part._cut_feature_span(feature)
            if span is None:
                raise ModelError(
                    f"mate: cannot place the cylinder of {of_feature!r}; the cut "
                    "removes no material"
                )
            mid = (span[0] + span[1]) / 2.0 - frame.offset
        else:
            mid = feature.direction_sign * (feature.depth_mm or 0.0) / 2.0
        center_local = frame.to_world(circle.cx, circle.cy, mid)
        pick_local = frame.to_world(circle.cx + circle.r, circle.cy, mid)
        world_axis_dir = comp.transform.rotate(frame.normal)
        axis = max(range(3), key=lambda i: abs(world_axis_dir[i]))
        return ResolvedCylinder(
            component=component,
            axis=axis,
            center=comp.transform.apply(center_local),
            radius=circle.r,
            pick=comp.transform.apply(pick_local),
            feature_kind=feature.kind,
        )

    # -- mates ---------------------------------------------------------

    def _movable(self, a: ResolvedEntity, b: ResolvedEntity, axes: list[int]) -> str:
        """Pick which component the solver moves.

        The component with MORE free axes among those the mate needs moves
        (tie prefers b: "b moves to a"), so a mostly-pinned component is
        never dragged when a free one is available.
        """
        ca, cb = self.component(a.component), self.component(b.component)
        if a.component == b.component:
            raise ModelError(
                f"mate: both entities belong to component {a.component!r}"
            )

        def free(c: ComponentRec) -> int:
            return 0 if c.fixed else sum(1 for ax in axes if ax not in c.pinned)

        fa, fb = free(ca), free(cb)
        if fb >= fa and fb > 0:
            return cb.name
        if fa > 0:
            return ca.name
        # Both fully pinned on the needed axes: move nothing; _pin's
        # consistency check on either side decides redundant vs conflict.
        if not cb.fixed:
            return cb.name
        if not ca.fixed:
            return ca.name
        raise ModelError(
            "mate: both components are fixed; a mate between two fixed "
            "components can never be satisfied unless it is redundant — "
            "unfix one of them"
        )

    def _pin(self, mate_name: str, comp: ComponentRec, axis: int, value: float) -> bool:
        """Pin one translation axis; returns True if this added a new pin."""
        if axis in comp.pinned:
            if abs(comp.pinned[axis] - value) > 1e-3:
                raise ModelError(
                    f"{mate_name}: over-constrained — component {comp.name!r} is "
                    f"already pinned at {_AXIS_NAMES[axis]}={comp.pinned[axis]:.3f} mm "
                    f"but this mate requires {_AXIS_NAMES[axis]}={value:.3f} mm "
                    f"(difference {abs(comp.pinned[axis] - value):.3f} mm). Check for "
                    "conflicting mates or mismatched hole patterns."
                )
            return False
        comp.pinned[axis] = value
        comp.transform = comp.transform.with_translation(
            tuple(
                value if i == axis else comp.transform.translation[i] for i in range(3)
            )  # type: ignore[arg-type]
        )
        return True

    def mate_picks(self, a: ResolvedEntity, b: ResolvedEntity) -> tuple[Vec3, Vec3]:
        """Pre-solve SelectByID2 pick points for a mate's two entities.

        Captured BEFORE solving: on real SolidWorks the components still
        sit at their pre-mate positions when the selections execute —
        AddMate5 itself performs the move. When both faces are already
        coplanar (a component inserted exactly at its mated position),
        coordinate picks cannot tell the two faces apart, so each pick is
        re-chosen to a point where only its own face exists (outside the
        other component's footprint, or inside one of its hole openings).
        """
        pick_a, pick_b = a.pick, b.pick
        if (
            isinstance(a, ResolvedFace)
            and isinstance(b, ResolvedFace)
            and a.axis == b.axis
            and abs(a.position - b.position) <= 1e-3
        ):
            ra = self._coplanar_pick(a, b)
            rb = self._coplanar_pick(b, a)
            pick_a = ra or pick_a
            pick_b = rb or pick_b
            if ra is None or rb is None:
                self._warn(
                    f"mate: faces of {a.component!r} and {b.component!r} are already "
                    "coplanar with overlapping footprints; coordinate selection "
                    "cannot fully distinguish them — insert the component with a "
                    "small standoff and let the mate close the gap"
                )
        return pick_a, pick_b

    def _coplanar_pick(self, face: ResolvedFace, other: ResolvedFace) -> Vec3 | None:
        """A pick on ``face`` where ``other``'s component has no face."""
        ocomp = self.component(other.component)
        axis = face.axis
        ca, cb = (i for i in range(3) if i != axis)
        olo, ohi = ocomp.world_aabb()
        p = face.pick

        def off_other(pt: Vec3) -> bool:
            return not (
                olo[ca] - EPS <= pt[ca] <= ohi[ca] + EPS
                and olo[cb] - EPS <= pt[cb] <= ohi[cb] + EPS
            )

        if off_other(p):
            return p
        # Try ring points inside the other component's through-hole openings
        # on the shared plane, clear of this face's own blockers (a shank).
        comp = self.component(face.component)
        if ocomp.part is None or comp.part is None:
            return None
        for hole_center, hole_r in _world_through_holes(ocomp, axis, face.position):
            inner = _coaxial_blocker_radius(comp, face, hole_center)
            if inner >= hole_r - EPS:
                continue  # no ring: own material fills the opening
            r_pick = (inner + hole_r) / 2.0 if inner > 0 else hole_r / 2.0
            candidate = list(hole_center)
            candidate[axis] = face.position
            candidate[ca] += r_pick
            cand = (candidate[0], candidate[1], candidate[2])
            if _on_face(comp, face, cand):
                return cand
        return None

    def mate(
        self,
        mate_type: str,
        a: ResolvedEntity,
        b: ResolvedEntity,
        value: float | None,
    ) -> MateRec:
        self._mate_n += 1
        name = f"Mate{self._mate_n}"
        if mate_type in ("coincident", "distance", "parallel"):
            if not (isinstance(a, ResolvedFace) and isinstance(b, ResolvedFace)):
                raise ModelError(f"{name}: {mate_type} mates need two planar faces")
            if a.axis != b.axis:
                raise ModelError(
                    f"{name}: faces are not parallel (normals along "
                    f"{_AXIS_NAMES[a.axis]} vs {_AXIS_NAMES[b.axis]}); with 90-degree "
                    "orientations these faces can never mate {0}".format(mate_type)
                )
            new_pin = self._solve_planar(name, mate_type, a, b, value)
        elif mate_type == "concentric":
            if not (isinstance(a, ResolvedCylinder) and isinstance(b, ResolvedCylinder)):
                raise ModelError(f"{name}: concentric mates need two cylindrical faces")
            if a.axis != b.axis:
                raise ModelError(
                    f"{name}: cylinder axes are not parallel "
                    f"({_AXIS_NAMES[a.axis]} vs {_AXIS_NAMES[b.axis]})"
                )
            new_pin = self._solve_concentric(name, a, b)
        elif mate_type == "width":
            raise ModelError(
                f"{name}: width mates are deferred in v0.3; use a distance or "
                "coincident pair instead"
            )
        else:  # pragma: no cover - schema restricts the values
            raise ModelError(f"{name}: unsupported mate type {mate_type!r}")
        if not new_pin:
            self._warn(
                f"{name}: redundant mate — it constrains nothing that earlier mates "
                "have not already pinned"
            )
        rec = MateRec(name=name, mate_type=mate_type, a=a, b=b, value=value)
        self.mates.append(rec)
        return rec

    def _solve_planar(
        self, name: str, mate_type: str, a: ResolvedFace, b: ResolvedFace, value: float | None
    ) -> bool:
        axis = a.axis
        mover = self._movable(a, b, [axis])
        if mate_type == "parallel":
            new_lock = False
            for c in (self.component(a.component), self.component(b.component)):
                if not c.fixed:
                    for ax in range(3):
                        if ax != axis and ax not in c.locked_rot:
                            c.locked_rot.add(ax)
                            new_lock = True
            return new_lock
        offset = 0.0
        if mate_type == "distance":
            # Separate the faces by `value` along the STATIC face's outward
            # normal, which keeps the parts on opposite sides of the gap.
            offset = value or 0.0
        static, moving = (a, b) if mover == b.component else (b, a)
        target_plane = static.position + (offset * static.normal_sign)
        comp = self.component(moving.component)
        delta = target_plane - moving.position
        new_pin = self._pin(name, comp, axis, comp.transform.translation[axis] + delta)
        if new_pin:
            _shift_entity(moving, axis, delta)
        for ax in range(3):
            if ax != axis and ax not in comp.locked_rot:
                comp.locked_rot.add(ax)
                new_pin = True
        if a.normal_sign == b.normal_sign and mate_type in ("coincident", "distance"):
            self._warn(
                f"{name}: face normals point the same way; SolidWorks will use the "
                "ALIGNED configuration, which usually means the parts overlap — "
                "check the component orientations"
            )
        return new_pin

    def _solve_concentric(self, name: str, a: ResolvedCylinder, b: ResolvedCylinder) -> bool:
        axis = a.axis
        cross = [ax for ax in range(3) if ax != axis]
        mover = self._movable(a, b, cross)
        static, moving = (a, b) if mover == b.component else (b, a)
        comp = self.component(moving.component)
        new_pin = False
        deltas: dict[int, float] = {}
        for ax in cross:
            delta = static.center[ax] - moving.center[ax]
            if self._pin(name, comp, ax, comp.transform.translation[ax] + delta):
                new_pin = True
                deltas[ax] = delta
        for ax, delta in deltas.items():
            _shift_entity(moving, ax, delta)
        # A second concentric about a DIFFERENT parallel axis line kills the
        # remaining rotation about the shared axis.
        anchor = tuple(
            static.center[i] if i != axis else 0.0 for i in range(3)
        )
        for prev_axis, prev_anchor in comp.concentric_anchors:
            distinct = (
                sum((prev_anchor[i] - anchor[i]) ** 2 for i in range(3)) > EPS
            )
            if prev_axis == axis and distinct and axis not in comp.locked_rot:
                comp.locked_rot.add(axis)
                new_pin = True
        comp.concentric_anchors.append((axis, anchor))  # type: ignore[arg-type]
        for ax in cross:
            if ax not in comp.locked_rot:
                comp.locked_rot.add(ax)
                new_pin = True
        kinds = {a.feature_kind, b.feature_kind}
        if kinds == {"boss", "cut"}:
            shank, hole = (a, b) if a.feature_kind == "boss" else (b, a)
            if shank.radius >= hole.radius - EPS:
                self._warn(
                    f"{name}: the {2 * shank.radius} mm shank does not clear the "
                    f"{2 * hole.radius} mm hole — an interference or zero-clearance "
                    "fit; fasteners need a clearance hole"
                )
        return new_pin

    # -- save / summary ------------------------------------------------

    def save_assembly(self, path: str) -> None:
        import os

        if not self.components:
            self._warn("save_assembly: the assembly has no components yet")
        for comp in self.components.values():
            if comp.fixed:
                continue
            free_t = comp.free_translations()
            free_r = comp.free_rotations()
            if not free_t and len(free_r) == 1:
                spin_axis = _AXIS_NAMES.index(free_r[0])
                if any(ax == spin_axis for ax, _ in comp.concentric_anchors):
                    # Free to spin about its own mated axis — the normal
                    # state for a fastener; informational, not a defect.
                    self._warn(
                        f"save_assembly: component {comp.name!r} can spin about its "
                        f"{free_r[0]} axis (normal for fasteners)"
                    )
                    continue
            if free_t or free_r:
                bits = []
                if free_t:
                    bits.append(f"translation {'/'.join(free_t)}")
                if free_r:
                    bits.append(f"rotation {'/'.join(free_r)}")
                self._warn(
                    f"save_assembly: component {comp.name!r} is under-constrained "
                    f"(free {', '.join(bits)})"
                )
        if not os.path.isabs(path):
            self._warn(
                f"save_assembly: '{path}' is relative; the COM backend resolves it "
                "against the swpilot working directory"
            )
        self.saved_to.append(path)

    def summary(self) -> dict[str, object]:
        return {
            "document": self.name,
            "kind": "assembly",
            "components": [
                {
                    "name": c.name,
                    "source": c.source,
                    "external": c.part is None,
                    "fixed": c.fixed,
                    "translation": list(c.transform.translation),
                    "rotation_row_major": c.transform.to_row_major(),
                    "free_translations": [] if c.fixed else c.free_translations(),
                    "free_rotations": [] if c.fixed else c.free_rotations(),
                }
                for c in self.components.values()
            ],
            "mates": [
                {
                    "name": m.name,
                    "type": m.mate_type,
                    "a": m.a.to_dict(),
                    "b": m.b.to_dict(),
                    **({"value": m.value} if m.value is not None else {}),
                }
                for m in self.mates
            ],
            "saved_to": list(self.saved_to),
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _dir_to_facing(d: Vec3) -> str:
    axis = max(range(3), key=lambda i: abs(d[i]))
    return f"{'+' if d[axis] > 0 else '-'}{_AXIS_NAMES[axis]}"


def _part_aabb(comp: ComponentRec) -> tuple[Vec3, Vec3]:
    return comp.local_aabb()


def _face_blockers(
    part: ModelTracker, l_axis: int, l_sign: float, l_pos: float, owner: str | None
) -> list[tuple[g.Shape, str]]:
    """Regions of the face plane where the face itself is absent.

    Holes (through-cut footprints on the same plane family) plus the
    footprints of boss features whose material extends PAST the face
    plane in its outward direction — there the surface belongs to the
    adjoining boss (a shank growing out of a bolt head's seat face), not
    to this face. A boss whose cap IS the face never blocks it.
    """
    from swpilot.model.planes import FAMILY_FOR_AXIS

    family = FAMILY_FOR_AXIS[_AXIS_NAMES[l_axis]]  # type: ignore[index]
    out: list[tuple[g.Shape, str]] = [
        (shape, family) for _name, shape in part._removed_footprints(family)
    ]
    for f in part.features:
        if f.sketch is None or f.sketch.frame.family != family or f.name == owner:
            continue
        if f.kind == "boss":
            o = f.sketch.frame.offset
            a, b = sorted((o, o + f.direction_sign * (f.depth_mm or 0.0)))
            past_face = b > l_pos + EPS if l_sign > 0 else a < l_pos - EPS
            if past_face:
                out.extend((shape, family) for shape in f.sketch.entities)
        elif f.kind == "cut" and not f.through_all:
            # A blind pocket/counterbore OPENING on this face removes the
            # face there (through-cuts are already covered globally).
            span = part._cut_feature_span(f)
            if span is None:
                continue
            opens_here = (
                abs(span[1] - l_pos) <= EPS if l_sign > 0 else abs(span[0] - l_pos) <= EPS
            )
            if opens_here:
                out.extend((shape, family) for shape in f.sketch.entities)
    return out


def _face_material(
    part: ModelTracker, l_axis: int, l_sign: float, l_pos: float, owner: str | None
) -> list[tuple[g.Shape, str]]:
    """Footprints of bosses whose cap actually forms this face — a valid
    pick must land on one of them (not in the void between disjoint
    bosses)."""
    from swpilot.model.planes import FAMILY_FOR_AXIS

    family = FAMILY_FOR_AXIS[_AXIS_NAMES[l_axis]]  # type: ignore[index]
    out: list[tuple[g.Shape, str]] = []
    for f in part.features:
        if f.kind != "boss" or f.sketch is None or f.sketch.frame.family != family:
            continue
        if owner is not None and f.name == owner:
            out.extend((shape, family) for shape in f.sketch.entities)
            continue
        o = f.sketch.frame.offset
        a, b = sorted((o, o + f.direction_sign * (f.depth_mm or 0.0)))
        cap_here = abs(b - l_pos) <= EPS if l_sign > 0 else abs(a - l_pos) <= EPS
        if cap_here:
            out.extend((shape, family) for shape in f.sketch.entities)
    return out


def _project_uv(part: ModelTracker, family: str, point: Vec3) -> tuple[float, float]:
    """World(local-part) point -> that family's sketch (u, v)."""
    from swpilot.model.planes import PlaneFrame

    frame = PlaneFrame(name=family, family=family, offset=0.0)  # type: ignore[arg-type]
    from swpilot.model.planes import dot

    return dot(point, frame.u), dot(point, frame.v)


def _world_through_holes(
    comp: ComponentRec, axis: int, plane_pos: float
) -> list[tuple[Vec3, float]]:
    """World (center, radius) of the component's through-hole openings
    whose removed span covers ``plane_pos`` along ``axis``."""
    assert comp.part is not None
    out: list[tuple[Vec3, float]] = []
    for f in comp.part.features:
        if f.kind != "cut" or not f.through_all or f.sketch is None:
            continue
        span = comp.part._cut_feature_span(f)
        if span is None:
            continue
        frame = f.sketch.frame
        world_normal = comp.transform.rotate(frame.normal)
        if abs(world_normal[axis]) < 0.5:
            continue
        for shape in f.sketch.entities:
            if not isinstance(shape, g.Circle):
                continue
            mid = (span[0] + span[1]) / 2.0 - frame.offset
            center = comp.transform.apply(frame.to_world(shape.cx, shape.cy, mid))
            ends = [
                comp.transform.apply(
                    frame.to_world(shape.cx, shape.cy, s_end - frame.offset)
                )[axis]
                for s_end in span
            ]
            if min(ends) - EPS <= plane_pos <= max(ends) + EPS:
                out.append((center, shape.r))
    return out


def _coaxial_blocker_radius(
    comp: ComponentRec, face: ResolvedFace, world_center: Vec3
) -> float:
    """Largest radius of this component's coaxial circles that BLOCK the
    face at ``world_center`` — bosses extending past the face plane in
    its outward direction (a shank in the middle of a bolt-head seat).
    The face's own outline never counts."""
    assert comp.part is not None
    axis = face.axis
    best = 0.0
    ca, cb = (i for i in range(3) if i != axis)
    for f in comp.part.features:
        if f.sketch is None or f.kind != "boss":
            continue
        frame = f.sketch.frame
        world_normal = comp.transform.rotate(frame.normal)
        if abs(world_normal[axis]) < 0.5:
            continue
        ends = [
            comp.transform.apply(frame.to_world(0.0, 0.0, n))[axis]
            for n in (0.0, f.direction_sign * (f.depth_mm or 0.0))
        ]
        past_face = (
            max(ends) > face.position + EPS
            if face.normal_sign > 0
            else min(ends) < face.position - EPS
        )
        if not past_face:
            continue
        for shape in f.sketch.entities:
            if not isinstance(shape, g.Circle):
                continue
            c = comp.transform.apply(frame.to_world(shape.cx, shape.cy, 0.0))
            if abs(c[ca] - world_center[ca]) <= EPS and abs(c[cb] - world_center[cb]) <= EPS:
                best = max(best, shape.r)
    return best


def _on_face(comp: ComponentRec, face: ResolvedFace, world_pt: Vec3) -> bool:
    """Does the face actually exist at this world point?

    In-plane AABB containment AND not inside the component's own removed
    material (its own through-holes) — identical stacked plates share
    hole positions, so a candidate over the other part's hole may sit in
    this part's hole void too.
    """
    lo, hi = comp.world_aabb()
    for i in range(3):
        if i == face.axis:
            continue
        if not (lo[i] - EPS <= world_pt[i] <= hi[i] + EPS):
            return False
    if comp.part is not None:
        from swpilot.model.planes import FAMILY_FOR_AXIS

        t = comp.transform.translation
        local = comp.transform.rotate_back(
            (world_pt[0] - t[0], world_pt[1] - t[1], world_pt[2] - t[2])
        )
        local_axis_dir = comp.transform.rotate_back(
            tuple(1.0 if i == face.axis else 0.0 for i in range(3))  # type: ignore[arg-type]
        )
        l_axis = max(range(3), key=lambda i: abs(local_axis_dir[i]))
        family = FAMILY_FOR_AXIS[_AXIS_NAMES[l_axis]]  # type: ignore[index]
        pu, pv = _project_uv(comp.part, family, local)
        for _name, shape in comp.part._removed_footprints(family):
            if g.covers(shape, g.Circle(pu, pv, 0.2)):
                return False
    return True


def _shift_entity(e: ResolvedEntity, axis: int, delta: float) -> None:
    """Keep a resolved entity's coordinates in sync after its component moved."""

    def shifted(p: Vec3) -> Vec3:
        return tuple(p[i] + (delta if i == axis else 0.0) for i in range(3))  # type: ignore[return-value]

    if isinstance(e, ResolvedFace):
        if e.axis == axis:
            e.position += delta
        e.pick = shifted(e.pick)
    else:
        e.center = shifted(e.center)
        e.pick = shifted(e.pick)
