"""Tests for the shared EOSdash components."""

import pytest
from fasthtml.common import to_xml

from akkudoktoreos.server.dash.components import (
    STATUS_TONES,
    Error,
    Info,
    Success,
    UpdateError,
)


class TestStatusMessages:
    """Status messages use the colors of the EOSdash theme, readable in light and dark mode."""

    @pytest.mark.parametrize(
        ("component", "tone"),
        [(Success, "success"), (Info, "info"), (Error, "error")],
    )
    def test_message_uses_the_theme_colors(self, component, tone):
        html = to_xml(component("Saved battery profile 'x'."))
        box_cls, icon_cls = STATUS_TONES[tone]
        assert 'role="alert"' in html
        assert "text-foreground" in html
        assert box_cls in html
        assert icon_cls in html
        assert "Saved battery profile" in html

    @pytest.mark.parametrize("component", [Success, Info, Error, UpdateError])
    def test_no_daisy_alert_colors(self, component):
        """daisyUI's alert colors come from its own theme: dark text on green or gray."""
        html = to_xml(component("message"))
        assert "alert-success" not in html
        assert "alert-info" not in html
        assert "alert-error" not in html

    def test_update_error_keeps_the_details(self):
        html = to_xml(UpdateError("first line\nsecond line"))
        assert "<details" in html
        assert "first line" in html
        assert STATUS_TONES["error"][0] in html
