"""EOSdash in a real (headless) browser.

These tests run only with ``pytest --browser-test`` (marker ``browser``) and need
the browser: ``uv run playwright install chromium``. They start EOS (which
starts EOSdash) with temporary directories and check what unit tests cannot
see: that the pages render in a browser, in light and dark mode, without
JavaScript errors. This is also the place for checks of htmx page actions,
BokehJS plots and their dark mode.

Keep these tests few and coarse; test the logic of the pages in unit tests.
"""

import time
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
import requests

pytestmark = pytest.mark.browser

# Console messages that are known and not caused by the page under test.
KNOWN_CONSOLE_NOISE = (
    # Tailwind is loaded from its CDN.
    "cdn.tailwindcss.com should not be used in production",
    # The menu tabs include the search box of the Config page in their requests;
    # on the other pages htmx reports that it is missing.
    'The selector "#config-search" on hx-include returned no matches',
)

# Time to wait for EOSdash (started by EOS after EOS is up) and for page actions [ms].
EOSDASH_STARTUP_S = 60
PAGE_TIMEOUT_MS = 30000


class DashPage:
    """A browser page on EOSdash that records JavaScript errors."""

    def __init__(self, page: Any, base_url: str) -> None:
        self.page = page
        self.base_url = base_url
        self.errors: list[str] = []
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("console", self._console)

    def _console(self, message: Any) -> None:
        if message.type != "error":
            return
        if any(noise in message.text for noise in KNOWN_CONSOLE_NOISE):
            return
        self.errors.append(f"console: {message.text}")

    def open(self) -> None:
        self.page.goto(f"{self.base_url}/", wait_until="networkidle")

    def tab_paths(self) -> list[str]:
        """Paths of the menu tabs."""
        return self.page.eval_on_selector_all(
            "button[hx-get^='/eosdash/']", "buttons => buttons.map(b => b.getAttribute('hx-get'))"
        )

    def open_tab(self, path: str) -> None:
        """Click a menu tab and wait until its content is loaded."""
        # The tabs add query values (e.g. dark=true): compare the path only.
        with self.page.expect_response(lambda response: urlsplit(response.url).path == path):
            self.page.click(f"button[hx-get='{path}']")
        self.page.wait_for_load_state("networkidle")


def wait_for_eosdash(base_url: str) -> None:
    """Wait until EOSdash answers; EOS starts it after its own startup."""
    deadline = time.monotonic() + EOSDASH_STARTUP_S
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/eosdash/health", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    pytest.fail(f"EOSdash at {base_url} did not start within {EOSDASH_STARTUP_S} s")


@pytest.fixture(scope="class")
def servers(server_setup_for_class) -> dict[str, Any]:
    """EOS and EOSdash, started with temporary directories for the test class."""
    wait_for_eosdash(str(server_setup_for_class["eosdash_server"]))
    return server_setup_for_class


class TestEOSdashInBrowser:
    @pytest.fixture
    def dash(self, servers, chromium, request) -> Iterator[DashPage]:
        """A fresh page; parametrize ``dash`` indirectly with "light" or "dark" for the color scheme."""
        color_scheme = getattr(request, "param", "light")
        context = chromium.new_context(
            viewport={"width": 1400, "height": 1000}, color_scheme=color_scheme
        )
        page = DashPage(context.new_page(), str(servers["eosdash_server"]))
        page.open()
        yield page
        context.close()

    @pytest.mark.parametrize("dash", ["light", "dark"], indirect=True)
    def test_every_tab_renders_without_errors(self, dash):
        paths = dash.tab_paths()
        assert paths
        for path in paths:
            dash.open_tab(path)
            assert dash.page.inner_html("#page-content").strip(), path
        assert dash.errors == []
