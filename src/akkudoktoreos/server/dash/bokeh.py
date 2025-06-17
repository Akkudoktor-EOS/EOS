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


def bokey_apply_theme_to_plot(plot: Plot, dark: bool) -> None:
    """Apply a dark or light theme to a Bokeh plot.

    This function modifies the appearance of a Bokeh `Plot` object in-place,
    adjusting background, border, title, axis, and grid colors based on the
    `dark` parameter.

    Args:
        plot (Plot): The Bokeh plot to style.
        dark (bool): Whether to apply the dark theme (`True`) or light theme (`False`).

    Notes:
        - This only affects the plot passed in; it does not change other plots
          in the same document.
    """
    if dark:
        plot.background_fill_color = "#1e1e1e"
        plot.border_fill_color = "#1e1e1e"
        plot.title.text_color = "white"
        for ax in plot.xaxis + plot.yaxis:
            ax.axis_line_color = "white"
            ax.major_tick_line_color = "white"
            ax.major_label_text_color = "white"
            ax.axis_label_text_color = "white"
        # Grid lines
        for grid in plot.renderers:
            if hasattr(grid, "grid_line_color"):
                grid.grid_line_color = "#333"
        for grid in plot.xgrid + plot.ygrid:
            grid.grid_line_color = "#333"
    else:
        plot.background_fill_color = "white"
        plot.border_fill_color = "white"
        plot.title.text_color = "black"
        for ax in plot.xaxis + plot.yaxis:
            ax.axis_line_color = "black"
            ax.major_tick_line_color = "black"
            ax.major_label_text_color = "black"
            ax.axis_label_text_color = "black"
        # Grid lines
        for grid in plot.renderers:
            if hasattr(grid, "grid_line_color"):
                grid.grid_line_color = "#ddd"
        for grid in plot.xgrid + plot.ygrid:
            grid.grid_line_color = "#ddd"


def Bokeh(plot: Plot, header: Optional[str] = None) -> Card:
    """Convert a Bokeh plot to a FastHTML FT component."""
    script, div = components(plot)
    if header:
        header = H4(header, cls="mt-2")
    return Card(
        NotStr(div),
        NotStr(script),
        header=header,
    )
