# Module taken from https://github.com/koaning/fh-altair
# MIT license

from typing import Optional

from bokeh.embed import components
from bokeh.models import Plot
from monsterui.franken import H4, Card, NotStr, Script

BokehJS = [
    Script(src="https://cdn.bokeh.org/bokeh/release/bokeh-3.6.3.min.js", crossorigin="anonymous"),
    Script(
        src="https://cdn.bokeh.org/bokeh/release/bokeh-widgets-3.6.3.min.js",
        crossorigin="anonymous",
    ),
    Script(
        src="https://cdn.bokeh.org/bokeh/release/bokeh-tables-3.6.3.min.js", crossorigin="anonymous"
    ),
    Script(
        src="https://cdn.bokeh.org/bokeh/release/bokeh-gl-3.6.3.min.js", crossorigin="anonymous"
    ),
    Script(
        src="https://cdn.bokeh.org/bokeh/release/bokeh-mathjax-3.6.3.min.js",
        crossorigin="anonymous",
    ),
]


def Bokeh(plot: Plot, header: Optional[str] = None) -> Card:
    """Converts an Bokeh plot to a FastHTML FT component."""
    script, div = components(plot)
    if header:
        header = H4(header, cls="mt-2")
    return Card(
        NotStr(div),
        NotStr(script),
        header=header,
    )
