import importlib
import importlib.util
import inspect
import pkgutil
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from docutils import nodes
from docutils.core import publish_parts
from docutils.frontend import OptionParser
from docutils.parsers.rst import Directive, Parser, directives
from docutils.utils import Reporter, new_document
from sphinx.ext.napoleon import Config as NapoleonConfig
from sphinx.ext.napoleon.docstring import GoogleDocstring

DIR_PROJECT_ROOT = Path(__file__).absolute().parent.parent
DIR_DOCS = DIR_PROJECT_ROOT / "docs"

PACKAGE_NAME = "akkudoktoreos"

# ---------------------------------------------------------------------------
# Location ignore rules (regex)
# ---------------------------------------------------------------------------
# Locations to ignore (regex). Note the escaped dot for literal '.'
IGNORE_LOCATIONS = [
    r"\.__new__$",

    # Pydantic
    r"\.model_copy$",
    r"\.model_dump$",
    r"\.model_dump_json$",
    r"\.field_serializer$",
    r"\.field_validator$",
    r"\.model_validator$",
    r"\.computed_field$",
    r"\.Field$",
    r"\.FieldInfo.*",
    r"\.ComputedFieldInfo.*",
    r"\.PrivateAttr$",

    # pathlib
    r"\.Path.*",

    # MarkdownIt
    r"\.MarkdownIt.*",

    # FastAPI
    r"\.FastAPI.*",
    r"\.FileResponse.*",
    r"\.PdfResponse.*",
    r"\.HTTPException$",

    # bokeh
    r"\.bokeh.*",
    r"\.figure.*",
    r"\.ColumnDataSource.*",
    r"\.LinearAxis.*",
    r"\.Range1d.*",

    # BeautifulSoup
    r"\.BeautifulSoup.*",

    # ExponentialSmoothing
    r"\.ExponentialSmoothing.*",

    # Pendulum
    r"\.Date$",
    r"\.DateTime$",
    r"\.Duration$",

    # ABC
    r"\.abstractmethod$",

    # numpytypes
    r"\.NDArray$",

    # typing
    r"\.ParamSpec",
    r"\.TypeVar",
    r"\.Annotated",

    # contextlib
    r"\.asynccontextmanager$",

    # concurrent
    r"\.ThreadPoolExecutor.*",

    # asyncio
    r"\.Lock.*",

    # scipy
    r"\.RegularGridInterpolator.*",

    # pylogging
    r"\.InterceptHandler.filter$",

    # itertools
    r"\.chain$",

    # functools
    r"\.partial$",

]

# ---------------------------------------------------------------------------
# Error message ignore rules by location (regex)
# ---------------------------------------------------------------------------
IGNORE_ERRORS_BY_LOCATION = {
    r"^akkudoktoreos.*": [
        r"Unexpected possible title overline or transition.*",
    ],
}


# --- Use your global paths ---
conf_path = DIR_DOCS / "conf.py"

spec = importlib.util.spec_from_file_location("sphinx_conf", conf_path)
if spec is None:
    raise AssertionError(f"Can not import sphinx_conf from {conf_path}")
sphinx_conf = importlib.util.module_from_spec(spec)
sys.modules["sphinx_conf"] = sphinx_conf
if spec.loader is None:
    raise AssertionError(f"Can not import sphinx_conf from {conf_path}")
spec.loader.exec_module(sphinx_conf)

# Build NapoleonConfig with all options
napoleon_config = NapoleonConfig(
    napoleon_google_docstring=getattr(sphinx_conf, "napoleon_google_docstring", True),
    napoleon_numpy_docstring=getattr(sphinx_conf, "napoleon_numpy_docstring", False),
    napoleon_include_init_with_doc=getattr(sphinx_conf, "napoleon_include_init_with_doc", False),
    napoleon_include_private_with_doc=getattr(sphinx_conf, "napoleon_include_private_with_doc", False),
    napoleon_include_special_with_doc=getattr(sphinx_conf, "napoleon_include_special_with_doc", True),
    napoleon_use_admonition_for_examples=getattr(sphinx_conf, "napoleon_use_admonition_for_examples", False),
    napoleon_use_admonition_for_notes=getattr(sphinx_conf, "napoleon_use_admonition_for_notes", False),
    napoleon_use_admonition_for_references=getattr(sphinx_conf, "napoleon_use_admonition_for_references", False),
    napoleon_use_ivar=getattr(sphinx_conf, "napoleon_use_ivar", False),
    napoleon_use_param=getattr(sphinx_conf, "napoleon_use_param", True),
    napoleon_use_rtype=getattr(sphinx_conf, "napoleon_use_rtype", True),
    napoleon_preprocess_types=getattr(sphinx_conf, "napoleon_preprocess_types", False),
    napoleon_type_aliases=getattr(sphinx_conf, "napoleon_type_aliases", None),
    napoleon_attr_annotations=getattr(sphinx_conf, "napoleon_attr_annotations", True),
)


FENCE_RE = re.compile(r"^```(\w*)\s*$")


def replace_fenced_code_blocks(doc: str) -> tuple[str, bool]:
    """Replace fenced code blocks (```lang) in a docstring with RST code-block syntax.

    Returns:
        (new_doc, changed):
            new_doc: The docstring with replacements applied
            changed: True if any fenced block was replaced
    """
    out_lines = []
    inside = False
    lang = ""
    buffer: list[str] = []
    changed = False

    lines = doc.split("\n")

    for line in lines:
        stripped = line.strip()

        # Detect opening fence: ``` or ```python
        m = FENCE_RE.match(stripped)
        if m and not inside:
            inside = True
            lang = m.group(1) or ""
            # Write RST code-block header
            if lang:
                out_lines.append(f"    .. code-block:: {lang}")
            else:
                out_lines.append("    .. code-block::")
            out_lines.append("")  # blank line required by RST
            changed = True
            continue

        # Detect closing fence ```
        if stripped == "```" and inside:
            # Emit fenced code content with indentation
            for b in buffer:
                out_lines.append("    " + b)
            out_lines.append("")  # trailing blank line to close environment
            inside = False
            buffer = []
            continue

        if inside:
            buffer.append(line)
        else:
            out_lines.append(line)

    # If doc ended while still in fenced code, flush
    if inside:
        changed = True
        for b in buffer:
            out_lines.append("    " + b)
        out_lines.append("")
        inside = False

    return "\n".join(out_lines), changed


def prepare_docutils_for_sphinx():

    class NoOpDirective(Directive):
        has_content = True
        required_arguments = 0
        optional_arguments = 100
        final_argument_whitespace = True
        def run(self):
            return []

    for d in ["attribute", "data", "method", "function", "class", "event", "todo"]:
        directives.register_directive(d, NoOpDirective)


def validate_rst(text: str) -> list[tuple[int, str]]:
    """Validate a string as reStructuredText.

    Returns a list of tuples: (line_number, message).
    """
    if not text or not text.strip():
        return []

    warnings: list[tuple[int, str]] = []

    class RecordingReporter(Reporter):
        """Capture warnings/errors instead of halting."""
        def system_message(self, level, message, *children, **kwargs):
            line = kwargs.get("line", None)
            warnings.append((line or 0, message))
            return nodes.system_message(message, level=level, type=self.levels[level], *children, **kwargs)

    # Create default settings
    settings = OptionParser(components=(Parser,)).get_default_values()

    document = new_document("<docstring>", settings=settings)

    # Attach custom reporter
    document.reporter = RecordingReporter(
        source="<docstring>",
        report_level=1,  # capture warnings and above
        halt_level=100,  # never halt
        stream=None,
        debug=False
    )

    parser = Parser()
    parser.parse(text, document)

    return warnings


def iter_docstrings(package_name: str):
    """Yield docstrings of modules, classes, functions in the given package."""

    package = importlib.import_module(package_name)

    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        module = importlib.import_module(module_info.name)

        # Module docstring
        if module.__doc__:
            yield f"Module {module.__name__}", inspect.getdoc(module)

        # Classes + methods
        for _, obj in inspect.getmembers(module):
            if inspect.isclass(obj) or inspect.isfunction(obj):
                if obj.__doc__:
                    yield f"{module.__name__}.{obj.__name__}", inspect.getdoc(obj)

                # Methods of classes
                if inspect.isclass(obj):
                    for _, meth in inspect.getmembers(obj, inspect.isfunction):
                        if meth.__doc__:
                            yield f"{module.__name__}.{obj.__name__}.{meth.__name__}", inspect.getdoc(meth)


def map_converted_to_original(orig: str, conv: str) -> dict[int,int]:
    """Map original docstring line to converted docstring line.

    Returns:
        mapping: key = converted line index (0-based), value = original line index (0-based).
    """
    orig_lines = orig.splitlines()
    conv_lines = conv.splitlines()

    matcher = SequenceMatcher(None, orig_lines, conv_lines)
    line_map = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "replace"):
            for o, c in zip(range(i1, i2), range(j1, j2)):
                line_map[c] = o
        elif tag == "insert":
            for c in range(j1, j2):
                line_map[c] = max(i1 - 1, 0)
    return line_map


def test_all_docstrings_rst_compliant():
    """All docstrings must be valid reStructuredText."""
    failures = []

    for location, doc in iter_docstrings(PACKAGE_NAME):
        # Skip ignored locations
        if any(re.search(pat, location) for pat in IGNORE_LOCATIONS):
            continue

        # convert like sphinx napoleon does
        doc_converted = str(GoogleDocstring(doc, napoleon_config))

        # Register directives that sphinx knows - just to avaid errors
        prepare_docutils_for_sphinx()

        # Validate
        messages = validate_rst(doc_converted)
        if not messages:
            continue

        # Map converted line numbers back to original docstring
        line_map = map_converted_to_original(doc, doc_converted)

        # Filter messages
        filtered_messages = []
        ignore_msg_patterns = []
        for loc_pattern, patterns in IGNORE_ERRORS_BY_LOCATION.items():
            if re.search(loc_pattern, location):
                ignore_msg_patterns.extend(patterns)

        for conv_line, msg_text in messages:
                orig_line = line_map.get(conv_line - 1, conv_line - 1) + 1
                if any(re.search(pat, msg_text) for pat in ignore_msg_patterns):
                    continue
                filtered_messages.append((orig_line, msg_text))

        if filtered_messages:
            failures.append((location, filtered_messages, doc, doc_converted))

    # Raise AssertionError with nicely formatted output
    if failures:
        msg = "Invalid reST docstrings (see https://www.sphinx-doc.org/en/master/usage/extensions/example_google.html for valid format):\n"
        for location, errors, doc, doc_converted in failures:
            msg += f"\n--- {location} ---\n"
            msg += "\nConverted by Sphinx Napoleon:\n"
            doc_lines = doc_converted.splitlines()
            for i, line_content in enumerate(doc_lines, start=1):
                line_str = f"{i:2}"  # fixed-width
                msg += f"    L{line_str}: {line_content}\n"
            msg += "\nOriginal:\n"
            doc_lines = doc.splitlines()
            error_map = {line: err for line, err in errors}
            for i, line_content in enumerate(doc_lines, start=1):
                line_str = f"{i:2}"  # fixed-width
                if i in error_map:
                    msg += f">>> L{line_str}: {line_content}  <-- {error_map[i]}\n"
                else:
                    msg += f"    L{line_str}: {line_content}\n"
            doc_fixed, changed = replace_fenced_code_blocks(doc)
            if changed:
                msg += "\nImproved for fenced code blocks:\n"
                msg += '"""' + doc_fixed + '\n"""\n'
        msg += f"Total: {len(failures)} docstrings"

        raise AssertionError(msg)
