"""EOSdash in a real (headless) browser.

These tests run only with ``pytest --browser-test`` (marker ``browser``) and need
the browser: ``uv run --extra dev playwright install chromium``. They start EOS
(which starts EOSdash) with temporary directories and check what unit tests
cannot see: that the pages render in a browser, in light and dark mode, without
JavaScript errors. This is also the place for checks of htmx page actions,
BokehJS plots and their dark mode.

Keep these tests few and coarse; test the logic of the pages in unit tests.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Iterator, Literal
from urllib.parse import urlsplit

import pytest
import requests

if TYPE_CHECKING:
    from playwright.sync_api import Browser, ConsoleMessage, Page

pytestmark = pytest.mark.browser

# Console messages that are known and not caused by the page under test.
KNOWN_CONSOLE_NOISE = (
    # Tailwind is loaded from its CDN.
    "cdn.tailwindcss.com should not be used in production",
    # The menu tabs include the search box of the Config page in their requests;
    # on the other pages htmx reports that it is missing.
    'The selector "#config-search" on hx-include returned no matches',
)

# Time to wait for EOSdash (started by EOS after EOS is up) [s].
EOSDASH_STARTUP_S = 60
# Time to wait for page actions [ms].
PAGE_TIMEOUT_MS = 30000

# Element the menu tabs swap their content into.
PAGE_CONTENT_SELECTOR = "#page-content"


class DashPage:
    """A browser page on EOSdash that records JavaScript errors."""

    def __init__(self, page: Page, base_url: str) -> None:
        """Wrap a browser page and start recording errors.

        Args:
            page: Browser page to use.
            base_url: Base URL of EOSdash, e.g. ``http://127.0.0.1:8504``.
        """
        self.page = page
        self.base_url = base_url
        self.errors: list[str] = []
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("console", self._console)

    def _console(self, message: ConsoleMessage) -> None:
        """Record console errors that are not known noise."""
        if message.type != "error":
            return
        if any(noise in message.text for noise in KNOWN_CONSOLE_NOISE):
            return
        self.errors.append(f"console: {message.text}")

    def open(self) -> None:
        """Open the EOSdash start page and wait until it is loaded."""
        self.page.goto(f"{self.base_url}/", wait_until="networkidle")

    def tab_paths(self) -> list[str]:
        """Return the paths of the menu tabs."""
        paths: list[str] = self.page.eval_on_selector_all(
            "button[hx-get^='/eosdash/']", "buttons => buttons.map(b => b.getAttribute('hx-get'))"
        )
        return paths

    def page_content(self) -> str:
        """Return the HTML of the page content area."""
        return self.page.inner_html(PAGE_CONTENT_SELECTOR)

    def open_tab(self, path: str) -> int:
        """Click a menu tab and wait until its content is swapped in.

        The content area is emptied before the click, so content left over from
        the previous tab cannot be mistaken for the new tab: htmx does not swap
        error responses, which leaves the content area empty.

        htmx swaps the content in after the response has arrived. For a
        successful response this waits until the content area is filled again;
        ``wait_for_load_state()`` would not wait, as the page itself is loaded.

        Args:
            path: Path of the tab, as in its ``hx-get`` attribute.

        Returns:
            HTTP status of the tab request.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        self.page.eval_on_selector(PAGE_CONTENT_SELECTOR, "element => element.replaceChildren()")
        # The tabs add query values (e.g. dark=true): compare the path only.
        with self.page.expect_response(
            lambda response: urlsplit(response.url).path == path
        ) as response_info:
            self.page.click(f"button[hx-get='{path}']")
        response = response_info.value
        if response.ok:
            try:
                self.page.wait_for_function(
                    "selector => document.querySelector(selector)?.innerHTML.trim() !== ''",
                    arg=PAGE_CONTENT_SELECTOR,
                )
            except PlaywrightTimeoutError:
                # Nothing was swapped in; the caller's content check reports it.
                pass
        return response.status


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
def servers(server_setup_for_class: dict[str, Any]) -> dict[str, Any]:
    """EOS and EOSdash, started with temporary directories for the test class."""
    wait_for_eosdash(str(server_setup_for_class["eosdash_server"]))
    return server_setup_for_class


class TestEOSdashInBrowser:
    """EOSdash pages rendered in headless Chromium."""

    @pytest.fixture
    def dash(
        self, chromium: Browser, servers: dict[str, Any], request: pytest.FixtureRequest
    ) -> Iterator[DashPage]:
        """A fresh page; parametrize ``dash`` indirectly with "light" or "dark" for the color scheme.

        ``chromium`` comes before ``servers`` so that a missing browser fails before EOS is started.
        """
        color_scheme: Literal["light", "dark"] = (
            "dark" if getattr(request, "param", "light") == "dark" else "light"
        )
        context = chromium.new_context(
            viewport={"width": 1400, "height": 1000}, color_scheme=color_scheme
        )
        try:
            page = DashPage(context.new_page(), str(servers["eosdash_server"]))
            page.open()
            yield page
        finally:
            context.close()

    @pytest.mark.parametrize("dash", ["light", "dark"], indirect=True)
    def test_every_tab_renders_without_errors(self, dash: DashPage) -> None:
        """Every menu tab loads successfully and renders content without JavaScript errors."""
        paths = dash.tab_paths()
        assert paths
        for path in paths:
            status = dash.open_tab(path)
            assert status == 200, f"{path}: HTTP {status}"
            assert dash.page_content().strip(), f"{path}: no content rendered"
        assert dash.errors == []

    def test_setup_wizard_stays_on_step_after_config_update(self, dash: DashPage) -> None:
        """A configuration card in the setup wizard updates the value and returns to the wizard."""
        assert dash.open_tab("/eosdash/wizard") == 200
        dash.page.click("#wizard-next")
        dash.page.wait_for_selector("#wizard-back")

        dash.page.click("#wizard summary:has-text('general.latitude')")
        value_input = "#general-latitude-update-form input[type='text']"
        dash.page.fill(value_input, "48.137")
        with dash.page.expect_response(
            lambda response: (
                urlsplit(response.url).path == "/eosdash/configuration"
                and response.request.method == "PUT"
            )
        ) as response_info:
            dash.page.press(value_input, "Enter")
        assert response_info.value.ok
        dash.page.wait_for_function(
            "selector => document.querySelector(selector)?.innerHTML.includes('48.137')",
            arg=PAGE_CONTENT_SELECTOR,
        )

        # Still the location step of the wizard, not the Config page
        assert dash.page.locator("#wizard").count() == 1
        assert "Step 2 of" in dash.page_content()
        assert dash.errors == []
