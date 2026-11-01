"""EOS notifies ``ems.notify_url`` after a completed optimization."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.emsettings import EnergyManagementCommonSettings


def _receiver():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            received.append(
                {
                    "path": self.path,
                    "type": self.headers.get("Content-Type"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, received


def test_completed_optimization_is_posted():
    server, received = _receiver()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/eos/solution-ready"
        event = {"event": "optimization_completed", "duration_s": 12.5}
        thread = ems_module.notify_optimization_completed(url, event)
        assert thread is not None
        thread.join(timeout=10)
        assert received == [
            {"path": "/api/eos/solution-ready", "type": "application/json", "body": event}
        ]
    finally:
        server.shutdown()


def test_no_url_no_notification():
    assert ems_module.notify_optimization_completed(None, {"event": "x"}) is None
    assert ems_module.notify_optimization_completed("", {"event": "x"}) is None


def test_unreachable_receiver_is_logged_once(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(ems_module.logger, "warning", lambda *a, **k: warnings.append(a[0]))
    monkeypatch.setattr(ems_module, "_notify_failing", False)
    server, _ = _receiver()
    port = server.server_port
    server.shutdown()
    server.server_close()
    for _ in range(3):
        ems_module._post_notification(f"http://127.0.0.1:{port}/x", {"event": "x"})
    assert len(warnings) == 1


def test_notify_url_setting():
    assert EnergyManagementCommonSettings().notify_url is None
    assert EnergyManagementCommonSettings(notify_url="  ").notify_url is None
    assert (
        EnergyManagementCommonSettings(notify_url=" http://127.0.0.1:8080/a ").notify_url
        == "http://127.0.0.1:8080/a"
    )
    with pytest.raises(ValidationError):
        EnergyManagementCommonSettings(notify_url="file:///etc/passwd")
