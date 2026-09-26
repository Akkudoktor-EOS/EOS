"""Regression tests for browser-facing links in the EOSdash footer."""

from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from fasthtml.common import to_xml

from akkudoktoreos.server.dash.footer import Footer


@pytest.mark.parametrize(
    "bind_host,request_host,expected_host",
    [
        ("0.0.0.0", "energy.example.com", "energy.example.com"),
        ("::", "2001:db8::1", "[2001:db8::1]"),
        ("api.example.com", "dashboard.example.com", "api.example.com"),
    ],
)
def test_footer_docs_link_uses_reachable_host(bind_host, request_host, expected_host):
    """The browser link must not expose a wildcard bind address."""
    with patch("akkudoktoreos.server.dash.footer.get_alive", return_value="alive") as health:
        footer = BeautifulSoup(to_xml(Footer(bind_host, 8503, request_host)), "html.parser")

    link = footer.find("a", href=f"http://{expected_host}:8503/docs")
    assert link is not None
    assert link.get_text(strip=True) == f"EOS {expected_host}:8503"
    health.assert_called_once_with(bind_host, 8503)
