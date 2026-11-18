"""Speed up Python cross-reference resolution with a suffix index.

Sphinx resolves most Python cross-references in "fuzzy" search mode (every
reference created from a type annotation carries ``refspecific``). For a name
that is not found exactly, ``PythonDomain.find_obj`` then scans *all* known
objects for names ending in ``.<name>`` (sphinx/domains/python/__init__.py,
fuzzy branch of ``find_obj``). With the EOS API reference (about 22,000 objects
and 150,000 references) this linear scan takes about three quarters of the whole
documentation build, see https://github.com/sphinx-doc/sphinx/issues/11282.

This extension replaces the Python domain with a subclass that answers the
fuzzy search from an index instead of a scan. ``oname.endswith("." + name)``
holds exactly when ``name`` follows one of the dots in ``oname``, so the index
maps every such suffix to the object names, in the iteration order of the
domain's objects. The search therefore returns the same matches in the same
order as Sphinx, including the first match that Sphinx picks for an ambiguous
name. The rendered documentation does not change.

The subclass copies the logic of ``find_obj`` from the Sphinx version in
``VERIFIED_SPHINX_VERSION``. For any other Sphinx version the extension does
nothing and logs a note; ``TestPyXrefIndex`` in ``tests/test_docsphinx.py``
checks the equivalence against the installed Sphinx and must pass before the
version is raised.
"""

from collections.abc import Set
from typing import Any

import sphinx
from sphinx.application import Sphinx
from sphinx.domains.python import ObjectEntry, PythonDomain
from sphinx.environment import BuildEnvironment
from sphinx.util import logging

logger = logging.getLogger(__name__)

#: Sphinx version whose ``PythonDomain.find_obj`` this extension reproduces.
VERIFIED_SPHINX_VERSION: tuple[int, int, int] = (9, 0, 4)


class IndexedPythonDomain(PythonDomain):
    """Python domain whose fuzzy object search uses a suffix index.

    The index is built on the first fuzzy search and dropped whenever the
    domain changes its objects (``note_object``, ``clear_doc``,
    ``merge_domaindata``); Sphinx changes them nowhere else. As a second line
    of defence the index is rebuilt when the number of objects differs from
    the indexed one. A direct change of the objects mapping that keeps their
    number is not noticed.
    """

    _suffix_index: tuple[int, dict[str, list[str]]] | None = None

    def _drop_suffix_index(self) -> None:
        """Forget the suffix index; the next fuzzy search rebuilds it."""
        self._suffix_index = None

    def _suffixes(self) -> dict[str, list[str]]:
        """Return the suffix index of the current objects.

        Returns:
            Mapping of every text that follows a dot in an object name to the
            object names with that ending, in the order of ``self.objects``.
        """
        objects = self.objects
        if self._suffix_index is not None and self._suffix_index[0] == len(objects):
            return self._suffix_index[1]
        index: dict[str, list[str]] = {}
        for oname in objects:
            pos = oname.find(".")
            while pos != -1:
                index.setdefault(oname[pos + 1 :], []).append(oname)
                pos = oname.find(".", pos + 1)
        self._suffix_index = (len(objects), index)
        return index

    def note_object(
        self,
        name: str,
        objtype: str,
        node_id: str,
        aliased: bool = False,
        location: Any = None,
    ) -> None:
        """Note a Python object for cross-reference and drop the index."""
        self._drop_suffix_index()
        super().note_object(name, objtype, node_id, aliased=aliased, location=location)

    def clear_doc(self, docname: str) -> None:
        """Remove the objects of ``docname`` and drop the index."""
        self._drop_suffix_index()
        super().clear_doc(docname)

    def merge_domaindata(self, docnames: Set[str], otherdata: dict[str, Any]) -> None:
        """Merge objects from a parallel read process and drop the index."""
        self._drop_suffix_index()
        super().merge_domaindata(docnames, otherdata)

    def find_obj(
        self,
        env: BuildEnvironment,
        modname: str,
        classname: str,
        name: str,
        type: str | None,
        searchmode: int = 0,
    ) -> list[tuple[str, ObjectEntry]]:
        """Find a Python object like ``PythonDomain.find_obj``.

        Only the fuzzy branch of search mode 1 differs: it looks the name up in
        the suffix index instead of scanning all objects.

        Args:
            env: The build environment.
            modname: Module of the reference, if any.
            classname: Class of the reference, if any.
            name: Target name of the reference.
            type: Role of the reference, or ``None`` for any object type.
            searchmode: 1 for references marked ``refspecific``, else 0.

        Returns:
            Matching ``(name, object entry)`` pairs, in Sphinx's order.
        """
        if searchmode != 1:
            return super().find_obj(env, modname, classname, name, type, searchmode)

        name = name.removesuffix("()")
        if not name:
            return []

        if type is None:
            objtypes: list[str] | None = list(self.object_types)
        else:
            objtypes = self.objtypes_for_role(type)
        if objtypes is None:
            return []

        objects = self.objects
        if modname and classname:
            fullname = f"{modname}.{classname}.{name}"
            if fullname in objects and objects[fullname].objtype in objtypes:
                return [(fullname, objects[fullname])]
        if (
            modname
            and f"{modname}.{name}" in objects
            and objects[f"{modname}.{name}"].objtype in objtypes
        ):
            return [(f"{modname}.{name}", objects[f"{modname}.{name}"])]
        if name in objects and objects[name].objtype in objtypes:
            return [(name, objects[name])]
        return [
            (oname, objects[oname])
            for oname in self._suffixes().get(name, ())
            if objects[oname].objtype in objtypes
        ]


def setup(app: Sphinx) -> dict[str, Any]:
    """Install the indexed Python domain if the Sphinx version is verified.

    Args:
        app: The Sphinx application.

    Returns:
        Extension metadata; the extension is safe for parallel builds.
    """
    if tuple(sphinx.version_info[:3]) == VERIFIED_SPHINX_VERSION:
        app.add_domain(IndexedPythonDomain, override=True)
    else:
        logger.info(
            "pyxrefindex: Sphinx %s is not the verified version %s; "
            "using the unindexed Python cross-reference search.",
            sphinx.__display_version__,
            ".".join(map(str, VERIFIED_SPHINX_VERSION)),
        )
    return {"parallel_read_safe": True, "parallel_write_safe": True}
