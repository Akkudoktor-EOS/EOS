import re
import time
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from bs4 import BeautifulSoup

from akkudoktoreos.server.dash.context import EOSDASH_ROOT, ROOT_PATH, request_url_for

# -----------------------------------------------------
# URL filtering logic
# -----------------------------------------------------

ALLOWED_PREFIXES = [
    "/api/hassio_ingress/",
    "http://", "https://",            # external URLs
    "mailto:", "tel:",                # contact URLs
    "#",                              # anchor links
]


def is_allowed_prefix(url: str) -> bool:
    return any(url.startswith(p) for p in ALLOWED_PREFIXES)


ABSOLUTE_URL = re.compile(r"^/[^/].*")
RELATIVE_PARENT = re.compile(r"^\.\./")
WS_REGEX = re.compile(r'new\s+WebSocket\s*\(\s*[\'"]([^\'"]*)[\'"]')


# -----------------------------------------------------
# Core HTML parser
# -----------------------------------------------------

def scan_html_for_link_issues(html: str):
    soup = BeautifulSoup(html, "html.parser")

    found_absolute: list[str] = []
    found_relative_up: list[str] = []
    all_urls = []

    def add_issue(lst, tag, attr, value) -> None:
        lst.append(f"<{tag.name} {attr}='{value}'>")

    for tag in soup.find_all(True):
        for attr in ("href", "src", "action"):
            if attr not in tag.attrs:
                continue

            value = tag[attr]
            if not isinstance(value, str):
                continue

            all_urls.append(value)

            # (1) absolute URL
            if ABSOLUTE_URL.match(value) and not is_allowed_prefix(value):
                add_issue(found_absolute, tag, attr, value)

            # (2) relative going up
            if RELATIVE_PARENT.match(value):
                add_issue(found_relative_up, tag, attr, value)

    # (3) mixed usage check: both absolute + relative appear
    used_absolute = any(u.startswith("/") for u in all_urls if not is_allowed_prefix(u))
    used_relative = any(not u.startswith("/") for u in all_urls if not is_allowed_prefix(u))

    mixed_usage = used_absolute and used_relative

    # (4) detect absolute WebSocket URLs in JS
    ws_bad = []
    for m in WS_REGEX.findall(html):
        if m.startswith("/") and not is_allowed_prefix(m):
            ws_bad.append(m)

    return found_absolute, found_relative_up, mixed_usage, ws_bad


def collect_testable_routes(app):
    urls = []
    for r in app.routes:
        if not hasattr(r, "path"):
            continue
        path = r.path

        # skip API-style or binary endpoints:
        if path.startswith("/api"):
            continue
        if path.endswith(".js") or path.endswith(".css"):
            continue

        urls.append(path)
    return sorted(set(urls))


class TestEOSDash:

    def _assert_server_alive(self, base_url: str, timeout: int):
        """Poll the /eosdash/health endpoint until it's alive or timeout reached."""
        startup = False
        error = ""
        result = None

        for _ in range(int(timeout / 3)):
            try:
                result = requests.get(f"{base_url}/eosdash/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(3)

        assert startup, f"Connection to {base_url}/eosdash/health failed: {error}"
        assert result is not None
        assert result.json()["status"] == "alive"

    def test_eosdash_started(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server is started by EOS server."""
        eosdash_server = server_setup_for_class["eosdash_server"]
        timeout = server_setup_for_class["timeout"]
        self._assert_server_alive(eosdash_server, timeout)

    def test_eosdash_proxied_by_eos(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server proxied by EOS server."""
        server = server_setup_for_class["server"]
        timeout = server_setup_for_class["timeout"]
        self._assert_server_alive(server, timeout)

    def test_ingress_safe_links(self, server_setup_for_class, monkeypatch, tmp_path):
        base = server_setup_for_class["eosdash_server"]

        with patch("akkudoktoreos.server.dash.context.ROOT_PATH", "/api/hassio_ingress/TOKEN/"):
            eos_dir = tmp_path
            monkeypatch.setenv("EOS_DIR", str(eos_dir))
            monkeypatch.setenv("EOS_CONFIG_DIR", str(eos_dir))

            # Import with environment vars set to prevent creation of EOS.config.json in wrong dir.
            from akkudoktoreos.server.eosdash import app

            for path in collect_testable_routes(app):
                url = f"{base}{path}"
                resp = requests.get(url)
                resp.raise_for_status()

                abs_issues, rel_up_issues, mixed_usage, ws_issues = scan_html_for_link_issues(resp.text)

                #assert not abs_issues, (
                #    f"Forbidden absolute paths detected on {path}:\n" +
                #    "\n".join(abs_issues)
                #)

                assert not rel_up_issues, (
                    f"Relative paths navigating up (`../`) detected on {path}:\n" +
                    "\n".join(rel_up_issues)
                )

                assert not mixed_usage, f"Mixed absolute/relative linking detected on page {path}"

                assert not ws_issues, f"Forbidden WebSocket paths detected on {path}:\n" + "\n".join(ws_issues)

    @pytest.mark.parametrize(
        "root_path,path,expected",
        [
            ("/", "/eosdash/footer", "/eosdash/footer"),
            ("/", "eosdash/footer", "/eosdash/footer"),
            ("/", "footer", "/eosdash/footer"),
            ("/", "eosdash/assets/logo.png", "/eosdash/assets/logo.png"),
            ("/api/hassio_ingress/TOKEN/", "/api/hassio_ingress/TOKEN/eosdash/footer", "/api/hassio_ingress/TOKEN/eosdash/footer"),
            ("/api/hassio_ingress/TOKEN/", "/eosdash/footer", "/api/hassio_ingress/TOKEN/eosdash/footer"),
            ("/api/hassio_ingress/TOKEN/", "eosdash/footer", "/api/hassio_ingress/TOKEN/eosdash/footer"),
            ("/api/hassio_ingress/TOKEN/", "footer", "/api/hassio_ingress/TOKEN/eosdash/footer"),
            ("/api/hassio_ingress/TOKEN/", "assets/logo.png", "/api/hassio_ingress/TOKEN/eosdash/assets/logo.png"),
        ],
    )
    def test_request_url_for(self, root_path, path, expected):
        """Test that request_url_for produces absolute non-rewritable URLs.

        Args:
            root_path (str): Root path.
            path (str): Path passed to request_url_for().
            expected (str): Final produced path.
        """

        result = request_url_for(path, root_path = root_path)
        assert result == expected, (
            f"URL rewriting mismatch. "
            f"root_path={root_path}, path={path}, expected={expected}, got={result}"
        )

        # Test fallback to global var
        with patch("akkudoktoreos.server.dash.context.ROOT_PATH", root_path):
            result = request_url_for(path, root_path = None)

        assert result == expected, (
            f"URL rewriting mismatch. "
            f"root_path={root_path}, path={path}, expected={expected}, got={result}"
        )
