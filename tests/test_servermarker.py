"""The tests that start a real server are marked for selection and for pytest-xdist."""

from typing import Any, Optional, Union

import conftest
import pytest


class FakeItem:
    """Minimal stand-in for a collected test item."""

    def __init__(self, *fixturenames: str, marks: tuple[str, ...] = ()) -> None:
        self.fixturenames = fixturenames
        self.marks: dict[str, tuple[Any, ...]] = {name: () for name in marks}

    def get_closest_marker(self, name: str) -> Optional[tuple[Any, ...]]:
        """Return the arguments of the marker, or None if the item does not carry it."""
        return self.marks.get(name)

    def add_marker(self, marker: Union[str, pytest.MarkDecorator]) -> None:
        """Record the marker by its name, as pytest accepts a name or a decorator."""
        decorator = getattr(pytest.mark, marker) if isinstance(marker, str) else marker
        self.marks[decorator.name] = decorator.args


def marks_after_marking(
    *fixturenames: str, marks: tuple[str, ...] = ()
) -> dict[str, tuple[Any, ...]]:
    """Run the server marking for one item and return the markers it carries then."""
    item = FakeItem(*fixturenames, marks=marks)
    conftest.mark_server_tests([item])  # type: ignore[list-item]
    return item.marks


class TestServerMarker:
    """Marking of the tests that start an EOS or EOSdash server."""

    def test_test_using_xprocess_is_marked_and_grouped(self) -> None:
        """A test that uses the xprocess fixture starts a server on the fixed ports."""
        assert marks_after_marking("config_eos", "xprocess") == {
            "eos_server": (),
            "xdist_group": ("eos_server",),
        }

    def test_explicitly_marked_test_is_grouped(self) -> None:
        """A test that binds the ports without a fixture carries the marker itself."""
        assert marks_after_marking("config_eos", marks=("eos_server",)) == {
            "eos_server": (),
            "xdist_group": ("eos_server",),
        }

    def test_other_tests_are_left_alone(self) -> None:
        assert marks_after_marking("config_eos", "tmp_path") == {}

    def test_marking_runs_before_other_collection_hooks(self, request: pytest.FixtureRequest) -> None:
        """pytest-xdist reads the group in its own hook, which must run after the marking.

        pluggy calls the plain hook implementations from the end of this list. The marking
        and everything behind it must be tryfirst, so that no ordinary implementation, such
        as the one of pytest-xdist on a worker, runs before it. Wrappers are left out: they
        surround all plain implementations whatever their order.
        """
        hook = request.config.pluginmanager.hook.pytest_collection_modifyitems
        implementations = [
            implementation
            for implementation in hook.get_hookimpls()
            if not (implementation.hookwrapper or getattr(implementation, "wrapper", False))
        ]
        marking = [
            implementation
            for implementation in implementations
            if implementation.function is conftest.pytest_collection_modifyitems
        ]

        assert len(marking) == 1
        called_first = implementations[implementations.index(marking[0]) :]
        assert all(implementation.tryfirst for implementation in called_first)

    def test_no_unmarked_server_test_is_selected(self, request: pytest.FixtureRequest) -> None:
        """Every selected test of this session that uses xprocess carries both markers.

        A plain run checks all server tests this way. In a CI shard the session holds only
        the modules of that shard, and with working marking none of their server tests is
        selected; the test-count job checks the marking for the CI split.
        """
        for item in request.session.items:
            if "xprocess" in getattr(item, "fixturenames", ()):
                assert item.get_closest_marker("eos_server") is not None, item.nodeid
                group = item.get_closest_marker("xdist_group")
                assert group is not None and group.args == ("eos_server",), item.nodeid
