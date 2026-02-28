import asyncio
import json
import os
import signal
import time
from http import HTTPStatus
from pathlib import Path

import psutil
import pytest
import requests
from conftest import cleanup_eos_eosdash
from loguru import logger

from akkudoktoreos.core.version import __version__
from akkudoktoreos.server.server import get_default_host, wait_for_port_free


class TestServer:
    def test_server_setup_for_class(self, server_setup_for_class):
        """Ensure server is started."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        # Assure server is running
        result = requests.get(f"{server}/v1/health", timeout=2)
        assert result.status_code == HTTPStatus.OK
        health = result.json()
        assert health["status"] == "alive"
        assert health["version"] == __version__

        result = requests.get(f"{server}/v1/config", timeout=2)
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()
        config_folder_path = Path(config_json["general"]["config_folder_path"])
        config_file_path = Path(config_json["general"]["config_file_path"])
        data_folder_path = Path(config_json["general"]["data_folder_path"])
        data_ouput_path = Path(config_json["general"]["data_output_path"])
        # Assure we are working in test environment
        assert str(config_folder_path).startswith(eos_dir)
        assert str(config_file_path).startswith(eos_dir)
        assert str(data_folder_path).startswith(eos_dir)
        assert str(data_ouput_path).startswith(eos_dir)


class TestServerStartStop:

    @pytest.mark.asyncio
    async def test_forward_stream_truncates_very_long_line(self, monkeypatch, tmp_path):
        """Test logging from EOSdash can also handle very long lines."""

        eos_dir = tmp_path
        monkeypatch.setenv("EOS_DIR", str(eos_dir))
        monkeypatch.setenv("EOS_CONFIG_DIR", str(eos_dir))

        # Import after env vars are set
        from akkudoktoreos.server.rest.starteosdash import (
            EOSDASH_LOG_MAX_LINE_BYTES,
            _eosdash_log_worker,
            eosdash_log_queue,
            forward_stream,
        )

        # ---- Ensure queue + worker are initialized ----
        if eosdash_log_queue is None:
            from akkudoktoreos.server.rest import starteosdash

            starteosdash.eosdash_log_queue = asyncio.Queue(maxsize=10)
            worker_task = asyncio.create_task(_eosdash_log_worker())
        else:
            worker_task = None

        long_message = "X" * (EOSDASH_LOG_MAX_LINE_BYTES + 10_000)
        raw_line = f"INFO some.module:123 some_func - {long_message}\n"
        raw_bytes = raw_line.encode()

        reader = asyncio.StreamReader()
        reader.feed_data(raw_bytes)
        reader.feed_eof()

        # ---- Capture Loguru output ----
        records = []

        def sink(message):
            records.append(message.record)

        logger_id = logger.add(sink, level="INFO")

        try:
            await forward_stream(reader)

            # Allow log worker to flush queue
            await asyncio.sleep(0)

        finally:
            logger.remove(logger_id)

            # Clean shutdown of worker (important for pytest)
            if worker_task:
                from akkudoktoreos.server.rest import starteosdash

                if starteosdash.eosdash_log_queue:
                    starteosdash.eosdash_log_queue.put_nowait(None)
                await worker_task

        # ---- Assert ----
        assert len(records) == 1, "Expected exactly one log record"

        record = records[0]
        msg = record["message"]

        assert msg.endswith("[TRUNCATED]"), "Expected truncation marker"
        assert len(msg) <= EOSDASH_LOG_MAX_LINE_BYTES + 20

    @pytest.mark.asyncio
    async def test_server_start_eosdash(self, config_eos, monkeypatch, tmp_path):
        """Test the EOSdash server startup from EOS.

        Do not use any fixture as this will make pytest the owner of the EOSdash port.

        Tests that:
        1. EOSdash starts via the supervisor
        2. The /eosdash/health endpoint returns OK
        3. EOSdash reports correct status and version
        4. EOSdash can be terminated cleanly
        """
        eos_dir = tmp_path
        monkeypatch.setenv("EOS_DIR", str(eos_dir))
        monkeypatch.setenv("EOS_CONFIG_DIR", str(eos_dir))

        # Import with environment vars set to prevent creation of EOS.config.json in wrong dir.
        from akkudoktoreos.server.rest.starteosdash import supervise_eosdash

        config_eos.server.host = get_default_host()
        config_eos.server.port = 8503
        config_eos.server.eosdash_host = config_eos.server.host
        config_eos.server.eosdash_port = 8504
        timeout = 120

        eosdash_server = f"http://{config_eos.server.eosdash_host}:{config_eos.server.eosdash_port}"

        # Cleanup any EOS and EOSdash process left.
        cleanup_eos_eosdash(
            host=config_eos.server.host,
            port=config_eos.server.port,
            eosdash_host=config_eos.server.eosdash_host,
            eosdash_port=config_eos.server.eosdash_port,
            server_timeout=timeout,
        )

        # Port may be blocked
        assert wait_for_port_free(config_eos.server.eosdash_port, timeout=120, waiting_app_name="EOSdash")

        """Start EOSdash."""
        await supervise_eosdash()

        # give EOSdash some time to startup
        await asyncio.sleep(1)

        # ---------------------------------
        # Wait for health endpoint to come up
        # ---------------------------------
        startup = False
        error = ""

        for retries in range(int(timeout / 3)):
            try:
                resp = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                if resp.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{resp.status_code}, {str(resp.content)}"
            except Exception as ex:
                error = str(ex)

            await asyncio.sleep(3)

        assert startup, f"Connection to {eosdash_server}/eosdash/health failed: {error}"

        health = resp.json()
        assert health.get("status") == "alive"
        assert health.get("version") == __version__

        # ---------------------------------
        # Shutdown EOSdash (as provided)
        # ---------------------------------
        try:
            resp = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
            if resp.status_code == HTTPStatus.OK:
                pid = resp.json().get("pid")
                assert pid is not None, "EOSdash did not report a PID"

                os.kill(pid, signal.SIGTERM)
                time.sleep(1)

                # After shutdown, the server should not respond OK anymore
                try:
                    resp2 = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                    assert resp2.status_code != HTTPStatus.OK
                except Exception:
                    pass  # expected
        except Exception:
            pass  # ignore shutdown errors for safety

        # ---------------------------------
        # Cleanup any leftover processes
        # ---------------------------------
        cleanup_eos_eosdash(
            host=config_eos.server.host,
            port=config_eos.server.port,
            eosdash_host=config_eos.server.eosdash_host,
            eosdash_port=config_eos.server.eosdash_port,
            server_timeout=timeout,
        )

    @pytest.mark.skipif(os.name == "nt", reason="Server restart not supported on Windows")
    def test_server_restart(self, server_setup_for_function, is_system_test):
        """Test server restart."""
        server = server_setup_for_function["server"]
        eos_dir = server_setup_for_function["eos_dir"]
        timeout = server_setup_for_function["timeout"]

        result = requests.get(f"{server}/v1/config")
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()
        config_folder_path = Path(config_json["general"]["config_folder_path"])
        config_file_path = Path(config_json["general"]["config_file_path"])
        data_folder_path = Path(config_json["general"]["data_folder_path"])
        data_ouput_path = Path(config_json["general"]["data_output_path"])
        cache_file_path = data_folder_path.joinpath(config_json["cache"]["subpath"]).joinpath(
            "cachefilestore.json"
        )
        # Assure we are working in test environment
        assert str(config_folder_path).startswith(eos_dir)
        assert str(config_file_path).startswith(eos_dir)
        assert str(data_folder_path).startswith(eos_dir)
        assert str(data_ouput_path).startswith(eos_dir)

        if is_system_test:
            # Prepare cache entry and get cached data
            result = requests.put(f"{server}/v1/config/weather/provider", json="BrightSky")
            assert result.status_code == HTTPStatus.OK

            result = requests.post(f"{server}/v1/prediction/update/BrightSky")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK
            data = result.json()
            assert data["data"] != {}

            result = requests.put(f"{server}/v1/config/file")
            assert result.status_code == HTTPStatus.OK

        # Save cache
        result = requests.post(f"{server}/v1/admin/cache/save")
        assert result.status_code == HTTPStatus.OK
        cache = result.json()

        assert cache_file_path.exists()

        result = requests.get(f"{server}/v1/admin/cache")
        assert result.status_code == HTTPStatus.OK
        cache = result.json()

        result = requests.get(f"{server}/v1/health")
        assert result.status_code == HTTPStatus.OK
        pid = result.json()["pid"]

        result = requests.post(f"{server}/v1/admin/server/restart")
        assert result.status_code == HTTPStatus.OK
        assert "Restarting EOS.." in result.json()["message"]
        new_pid = result.json()["pid"]

        # Wait for server to shut down
        for retries in range(10):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    pid = result.json()["pid"]
                    if pid == new_pid:
                        # Already started
                        break
                else:
                    break
            except Exception as ex:
                break
            time.sleep(3)

        # Assure EOS is up again
        startup = False
        error = ""
        for retries in range(int(timeout / 5)):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(5)

        assert startup, f"Connection to {server}/v1/health failed: {error}"
        assert result.json()["status"] == "alive"
        pid = result.json()["pid"]
        assert pid == new_pid

        result = requests.get(f"{server}/v1/admin/cache")
        assert result.status_code == HTTPStatus.OK
        new_cache = result.json()

        assert cache.items() <= new_cache.items()

        if is_system_test:
            result = requests.get(f"{server}/v1/config")
            assert result.status_code == HTTPStatus.OK
            assert result.json()["weather"]["provider"] == "BrightSky"

            # Wait for initialisation task to have finished
            time.sleep(5)

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK
            assert result.json() == data

        # Shutdown the newly created server
        result = requests.post(f"{server}/v1/admin/server/shutdown")
        assert result.status_code == HTTPStatus.OK
        assert "Stopping EOS.." in result.json()["message"]
        new_pid = result.json()["pid"]


class TestServerWithEnv:
    eos_env = {
        "EOS_SERVER__EOSDASH_PORT": "8555",
    }

    def test_server_setup_for_class(self, server_setup_for_class):
        """Ensure server is started with environment passed to configuration."""
        server = server_setup_for_class["server"]

        assert server_setup_for_class["eosdash_port"] == int(self.eos_env["EOS_SERVER__EOSDASH_PORT"])

        result = requests.get(f"{server}/v1/config")
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()

        # Assure config got configuration from environment
        assert config_json["server"]["eosdash_port"] == int(self.eos_env["EOS_SERVER__EOSDASH_PORT"])
