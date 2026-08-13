"""SessionTracker tests: document naming, routing, warnings."""

import pytest

from swpilot.model.session import SessionTracker
from swpilot.model.tracker import ModelError


class TestDocuments:
    def test_auto_naming(self) -> None:
        s = SessionTracker()
        assert s.new_part(None)[0] == "Part1"
        assert s.new_part(None)[0] == "Part2"
        assert s.new_assembly(None)[0] == "Assembly1"

    def test_explicit_names_and_activation(self) -> None:
        s = SessionTracker()
        s.new_part("base")
        s.new_part("cover")
        assert s.active == "cover"
        s.activate("base")
        assert s.active == "base"

    def test_duplicate_name_rejected(self) -> None:
        s = SessionTracker()
        s.new_part("base")
        with pytest.raises(ModelError, match="already exists"):
            s.new_assembly("base")

    def test_activate_unknown_rejected(self) -> None:
        s = SessionTracker()
        with pytest.raises(ModelError, match="unknown document"):
            s.activate("nope")


class TestRouting:
    def test_part_command_on_assembly_rejected(self) -> None:
        s = SessionTracker()
        s.new_assembly("asm")
        with pytest.raises(ModelError, match="is an assembly"):
            s.active_part("create_sketch")

    def test_assembly_command_on_part_rejected(self) -> None:
        s = SessionTracker()
        s.new_part("base")
        with pytest.raises(ModelError, match="is a part"):
            s.active_assembly("insert_component")

    def test_no_document_open(self) -> None:
        s = SessionTracker()
        with pytest.raises(ModelError, match="no document is open"):
            s.active_part("create_sketch")

    def test_part_lookup_by_name(self) -> None:
        s = SessionTracker()
        s.new_part("base")
        s.new_assembly("asm")
        assert s.part("base", "insert_component") is s.documents["base"]
        with pytest.raises(ModelError, match="is an assembly, not a part"):
            s.part("asm", "insert_component")


class TestWarnings:
    def test_inactive_document_warnings_prefixed(self) -> None:
        s = SessionTracker()
        _, base = s.new_part("base")
        base._warn("something about base")
        s.new_part("cover")  # base is no longer active
        warnings = s.pop_warnings()
        assert any(w.startswith("[base]") for w in warnings)

    def test_saved_path_lookup(self) -> None:
        s = SessionTracker()
        _, base = s.new_part("base")
        assert s.part_saved_path("base", "insert_component") is None
        base.save_part("base.SLDPRT")
        assert s.part_saved_path("base", "insert_component") == "base.SLDPRT"
