# ============================================================
# SW-Pilot — SolidWorks AI Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Author & Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Session twin: named documents (parts, assemblies, drawings) and routing.

One command file drives many documents. Commands apply to the *active*
document; creating a document activates it, `activate_document` switches
explicitly. Commands hitting the wrong document kind fail with a clear
error naming both.
"""

from __future__ import annotations

from swpilot.model.assembly import AssemblyTracker
from swpilot.model.drawing import DrawingTracker
from swpilot.model.tracker import ModelError, ModelTracker

Document = ModelTracker | AssemblyTracker | DrawingTracker


def _doc_kind(doc: Document) -> str:
    if isinstance(doc, ModelTracker):
        return "part"
    if isinstance(doc, AssemblyTracker):
        return "assembly"
    return "drawing"


def _a_kind(doc: Document) -> str:
    kind = _doc_kind(doc)
    return f"an {kind}" if kind == "assembly" else f"a {kind}"


class SessionTracker:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.order: list[str] = []
        self.active: str | None = None
        self._part_n = 0
        self._assembly_n = 0
        self._drawing_n = 0

    # -- creation / activation -----------------------------------------

    def _register(self, name: str, doc: Document) -> None:
        if name in self.documents:
            raise ModelError(f"document name {name!r} already exists: {sorted(self.documents)}")
        self.documents[name] = doc
        self.order.append(name)
        self.active = name

    def new_part(self, name: str | None) -> tuple[str, ModelTracker]:
        self._part_n += 1
        resolved = name or f"Part{self._part_n}"
        tracker = ModelTracker()
        tracker.new_part()
        self._register(resolved, tracker)
        return resolved, tracker

    def new_assembly(self, name: str | None) -> tuple[str, AssemblyTracker]:
        self._assembly_n += 1
        resolved = name or f"Assembly{self._assembly_n}"
        tracker = AssemblyTracker(resolved)
        self._register(resolved, tracker)
        return resolved, tracker

    def new_drawing(
        self,
        name: str | None,
        of: str | None,
        sheet: str,
        scale: tuple[int, int] | None,
        projection: str,
        title: str | None,
        drawn_by: str,
        date: str,
    ) -> tuple[str, DrawingTracker]:
        if of is None:
            if self.active is None:
                raise ModelError(
                    "create_drawing: no document is open; build and save a part or "
                    "assembly first (or name one with 'of')"
                )
            of = self.active
        model = self.documents.get(of)
        if model is None:
            raise ModelError(
                f"create_drawing: unknown document {of!r}; existing: {sorted(self.documents)}"
            )
        if isinstance(model, DrawingTracker):
            raise ModelError(f"create_drawing: document {of!r} is itself a drawing")
        if not model.saved_to:
            kind = _doc_kind(model)
            save_op = "save_part" if kind == "part" else "save_assembly"
            raise ModelError(
                f"create_drawing: document {of!r} has not been saved; SolidWorks "
                f"drawing views reference the model file — add {save_op} first"
            )
        self._drawing_n += 1
        resolved = name or f"Drawing{self._drawing_n}"
        tracker = DrawingTracker(
            name=resolved,
            model_doc=of,
            model=model,
            model_path=model.saved_to[-1],
            sheet=sheet,
            scale=scale,
            projection=projection,
            title=title,
            drawn_by=drawn_by,
            date=date,
        )
        self._register(resolved, tracker)
        return resolved, tracker

    def activate(self, name: str) -> Document:
        if name not in self.documents:
            raise ModelError(
                f"activate_document: unknown document {name!r}; "
                f"existing: {sorted(self.documents)}"
            )
        self.active = name
        return self.documents[name]

    # -- routed access -------------------------------------------------

    def active_doc(self, op: str) -> tuple[str, Document]:
        if self.active is None:
            raise ModelError(f"{op}: no document is open; start with new_part or new_assembly")
        return self.active, self.documents[self.active]

    def active_part(self, op: str) -> ModelTracker:
        name, doc = self.active_doc(op)
        if not isinstance(doc, ModelTracker):
            raise ModelError(
                f"{op}: the active document {name!r} is {_a_kind(doc)}; part "
                "commands need an active part (activate_document or new_part first)"
            )
        return doc

    def active_assembly(self, op: str) -> AssemblyTracker:
        name, doc = self.active_doc(op)
        if not isinstance(doc, AssemblyTracker):
            raise ModelError(
                f"{op}: the active document {name!r} is {_a_kind(doc)}; assembly "
                "commands need an active assembly (new_assembly or "
                "activate_document first)"
            )
        return doc

    def active_drawing(self, op: str) -> DrawingTracker:
        name, doc = self.active_doc(op)
        if not isinstance(doc, DrawingTracker):
            raise ModelError(
                f"{op}: the active document {name!r} is {_a_kind(doc)}; drawing "
                "commands need an active drawing (create_drawing or "
                "activate_document first)"
            )
        return doc

    def part(self, name: str, op: str) -> ModelTracker:
        doc = self.documents.get(name)
        if doc is None:
            raise ModelError(f"{op}: unknown document {name!r}; existing: {sorted(self.documents)}")
        if not isinstance(doc, ModelTracker):
            raise ModelError(f"{op}: document {name!r} is {_a_kind(doc)}, not a part")
        return doc

    def part_saved_path(self, name: str, op: str) -> str | None:
        part = self.part(name, op)
        return part.saved_to[-1] if part.saved_to else None

    # -- warnings / lifecycle ------------------------------------------

    def pop_warnings(self) -> list[str]:
        out: list[str] = []
        for name in self.order:
            for w in self.documents[name].pop_warnings():
                out.append(w if name == self.active else f"[{name}] {w}")
        return out

    def finalize(self) -> None:
        for doc in self.documents.values():
            if isinstance(doc, ModelTracker):
                doc.finalize()

    def summary(self) -> dict[str, object]:
        if not self.documents:
            return {"documents": []}
        docs: list[dict[str, object]] = []
        for name in self.order:
            doc = self.documents[name]
            if isinstance(doc, ModelTracker):
                docs.append({"document": name, "kind": "part", **doc.summary()})
            else:
                docs.append(doc.summary())
        return {"active": self.active, "documents": docs}
