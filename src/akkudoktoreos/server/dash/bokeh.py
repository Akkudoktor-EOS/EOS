# Module taken from https://github.com/koaning/fh-altair
# MIT license
from typing import Optional

import bokeh
from bokeh.embed import components
from bokeh.models import Plot
from monsterui.franken import H4, Card, NotStr, Script

bokeh_version = bokeh.__version__

BokehJS = [
    Script(
        src=f"https://cdn.bokeh.org/bokeh/release/bokeh-{bokeh_version}.min.js",
        crossorigin="anonymous",
    ),
    Script(
        src=f"https://cdn.bokeh.org/bokeh/release/bokeh-widgets-{bokeh_version}.min.js",
        crossorigin="anonymous",
    ),
    Script(
        src=f"https://cdn.bokeh.org/bokeh/release/bokeh-tables-{bokeh_version}.min.js",
        crossorigin="anonymous",
    ),
    Script(
        src=f"https://cdn.bokeh.org/bokeh/release/bokeh-gl-{bokeh_version}.min.js",
        crossorigin="anonymous",
    ),
    Script(
        src=f"https://cdn.bokeh.org/bokeh/release/bokeh-mathjax-{bokeh_version}.min.js",
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
