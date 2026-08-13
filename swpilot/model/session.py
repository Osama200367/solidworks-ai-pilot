"""Session twin: named documents (parts + assemblies) and active routing.

One command file drives many documents. Commands apply to the *active*
document; creating a document activates it, `activate_document` switches
explicitly. Part-scoped commands hitting an assembly (or assembly
commands hitting a part) fail with a clear error naming both.
"""

from __future__ import annotations

from swpilot.model.assembly import AssemblyTracker
from swpilot.model.tracker import ModelError, ModelTracker

Document = ModelTracker | AssemblyTracker


class SessionTracker:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.order: list[str] = []
        self.active: str | None = None
        self._part_n = 0
        self._assembly_n = 0

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
                f"{op}: the active document {name!r} is an assembly; part commands "
                "need an active part (activate_document or new_part first)"
            )
        return doc

    def active_assembly(self, op: str) -> AssemblyTracker:
        name, doc = self.active_doc(op)
        if not isinstance(doc, AssemblyTracker):
            raise ModelError(
                f"{op}: the active document {name!r} is a part; assembly commands "
                "need an active assembly (new_assembly or activate_document first)"
            )
        return doc

    def part(self, name: str, op: str) -> ModelTracker:
        doc = self.documents.get(name)
        if doc is None:
            raise ModelError(f"{op}: unknown document {name!r}; existing: {sorted(self.documents)}")
        if not isinstance(doc, ModelTracker):
            raise ModelError(f"{op}: document {name!r} is an assembly, not a part")
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
