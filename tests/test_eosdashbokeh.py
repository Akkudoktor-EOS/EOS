"""Tests for the Bokeh helpers of EOSdash."""

from bokeh.models import LinearColorMapper
from bokeh.models.annotations import ColorBar
from bokeh.plotting import figure

from akkudoktoreos.server.dash.bokeh import bokey_apply_theme_to_plot


def _color_bar(plot: figure) -> ColorBar:
    (color_bar,) = plot.select(type=ColorBar)
    assert isinstance(color_bar, ColorBar)
    return color_bar


def _plot_with_legend_and_color_bar() -> figure:
    plot = figure()
    plot.line([1, 2], [3, 4], legend_label="power_w")
    plot.add_layout(ColorBar(color_mapper=LinearColorMapper(palette="Viridis256")), "right")
    return plot


class TestBokeyApplyThemeToPlot:
    def test_legend_and_color_bar_are_dark_in_dark_mode(self) -> None:
        plot = _plot_with_legend_and_color_bar()

        bokey_apply_theme_to_plot(plot, dark=True)

        (legend,) = plot.legend
        assert (legend.background_fill_color, legend.label_text_color) == ("#1e1e1e", "white")
        color_bar = _color_bar(plot)
        assert color_bar.background_fill_color == "#1e1e1e"
        assert (color_bar.major_label_text_color, color_bar.title_text_color) == ("white", "white")

    def test_legend_and_color_bar_keep_the_light_defaults_in_light_mode(self) -> None:
        plot = _plot_with_legend_and_color_bar()
        defaults = _plot_with_legend_and_color_bar()

        bokey_apply_theme_to_plot(plot, dark=False)

        (legend,), (default_legend,) = plot.legend, defaults.legend
        assert legend.background_fill_color == default_legend.background_fill_color
        assert legend.label_text_color == default_legend.label_text_color
        color_bar = _color_bar(plot)
        assert color_bar.background_fill_color == "#ffffff"
