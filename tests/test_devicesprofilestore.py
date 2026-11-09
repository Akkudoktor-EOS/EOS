"""Tests for the profilestore module.

Uses minimal, non-battery profile types to show that one store serves any
profile type given by parameter.
"""

import os
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import cachebox
import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.profile import profilestore as profilestore_module
from akkudoktoreos.devices.profile.profilestore import (
    ProfileDocument,
    ProfileEntry,
    ProfileStore,
    get_profile_store,
    profile_kind,
    profile_kinds,
    profile_type,
)


class DummyProfile(ProfileDocument[dict[str, Any]]):
    """Minimal profile document of a test profile kind."""

    profile_kind: ClassVar[str] = "dummy"

    model: str
    max_power_w: float

    def compile_profile(self, scale: float = 1.0, **options: Any) -> dict[str, Any]:
        """Compile into a plain dict."""
        return {"model": self.model, "power_w": self.max_power_w * scale}


class OtherProfile(ProfileDocument[str]):
    """Second test profile kind, to check that kinds are kept apart."""

    profile_kind: ClassVar[str] = "other"

    model: str

    def compile_profile(self, **options: Any) -> str:
        """Compile into the model name."""
        return self.model


@pytest.fixture
def store(config_eos, tmp_path, monkeypatch):
    """Return a fresh singleton store with the packaged directories in tmp_path.

    The packaged directories are moved so the package is not modified. The
    singleton is reset afterwards, so other tests get an unpatched store.
    """
    ProfileStore.reset_instance()
    store = get_profile_store()
    monkeypatch.setattr(
        store, "package_dir", lambda document_type: tmp_path / "package" / document_type.profile_kind
    )
    yield store
    ProfileStore.reset_instance()


def write_packaged(store: ProfileStore, name: str, document: ProfileDocument[Any]) -> Path:
    """Write a document to the packaged directory of its type."""
    path = store.package_dir(type(document)) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(), encoding="utf-8")
    return path


class TestSingleton:
    def test_store_is_a_singleton(self, store):
        assert get_profile_store() is store
        assert ProfileStore() is store

    def test_reset_gives_a_fresh_store_with_an_empty_cache(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=1.0))
        before = store.get(DummyProfile, "model-a")
        ProfileStore.reset_instance()
        fresh = get_profile_store()
        assert fresh is not store
        assert fresh._compiled == {}
        assert fresh.get(DummyProfile, "model-a") == before


class TestRegistry:
    def test_profile_types_are_registered_by_kind(self):
        assert profile_type("dummy") is DummyProfile
        assert profile_type("other") is OtherProfile
        assert {"dummy", "other"} <= set(profile_kinds())

    def test_battery_profile_is_registered(self):
        from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings

        assert profile_type("battery") is BatteryProfileSettings

    def test_unknown_kind_raises(self):
        with pytest.raises(KeyError, match="Unknown profile kind 'nope'"):
            profile_type("nope")

    def test_kind_of_another_type_cannot_be_taken(self):
        with pytest.raises(ValueError, match="already used by"):

            class Imposter(ProfileDocument[str]):
                profile_kind: ClassVar[str] = "dummy"

        assert profile_type("dummy") is DummyProfile

    def test_subclass_without_own_kind_is_not_registered(self):
        class Derived(DummyProfile):
            pass

        assert profile_type("dummy") is DummyProfile
        assert profile_kind(Derived) == "dummy"


class TestProfileKind:
    def test_directories_follow_kind(self, config_eos):
        store = get_profile_store()
        assert store.user_dir(DummyProfile) == (
            config_eos.general.data_folder_path / "profile" / "dummy"
        )
        assert store.package_dir(DummyProfile) == (
            config_eos.package_root_path / "data" / "profile" / "dummy"
        )

    @pytest.mark.parametrize("kind", ["", "Battery", "inverter-x", "../x", "1battery", 3])
    def test_invalid_kind_is_rejected_when_the_type_is_defined(self, kind):
        with pytest.raises(ValueError, match="Invalid profile kind"):

            class BadProfile(DummyProfile):
                profile_kind: ClassVar[Any] = kind

    def test_missing_kind_is_rejected(self, store):
        class NoKindProfile(ProfileDocument[str]):
            model: str

        with pytest.raises(ValueError, match="Invalid profile kind"):
            profile_kind(NoKindProfile)
        with pytest.raises(ValueError, match="Invalid profile kind"):
            store.save("model-a", NoKindProfile(model="a"))

    def test_kinds_are_kept_apart(self, store):
        store.save("model-a", DummyProfile(model="dummy", max_power_w=1.0))
        assert store.path(OtherProfile, "model-a")[1] is False
        store.save("model-a", OtherProfile(model="other"))
        assert store.get(OtherProfile, "model-a") == "other"
        assert store.get(DummyProfile, "model-a")["model"] == "dummy"

    def test_missing_compile_raises(self, store):
        class NoCompileProfile(ProfileDocument[str]):
            profile_kind: ClassVar[str] = "nocompile"
            model: str

        store.save("model-a", NoCompileProfile(model="a"))
        with pytest.raises(NotImplementedError, match="compile_profile"):
            store.get(NoCompileProfile, "model-a")


class TestProfileNames:
    @pytest.mark.parametrize(
        "name",
        [
            "../escape",
            "a/../../b",
            "/etc/passwd",
            "..\\escape",
            "vendor/model-a",
            # Drive relative on Windows: not absolute for PurePosixPath.
            "C:evil",
            ".hidden",
            "-dash",
            "name\n",
            "x" * 101,
        ],
    )
    def test_invalid_names_are_rejected(self, store, name):
        with pytest.raises(ValueError, match="Invalid dummy profile name"):
            store.path(DummyProfile, name)
        with pytest.raises(ValueError, match="Invalid dummy profile name"):
            store.save(name, DummyProfile(model="x", max_power_w=1.0))

    def test_empty_name_is_rejected(self, store):
        with pytest.raises(ValueError, match="Invalid dummy profile name"):
            store.path(DummyProfile, "")

    def test_reserved_name_is_rejected(self, store):
        """'schema' is the path of the JSON schema in the REST API."""
        with pytest.raises(ValueError, match="reserved"):
            store.save("schema", DummyProfile(model="x", max_power_w=1.0))

    @pytest.mark.parametrize("name", ["model-a", "Vendor_Model.v2", "9kwh"])
    def test_valid_names_are_files_in_the_profile_directory(self, store, name):
        path = store.save(name, DummyProfile(model="a", max_power_w=1.0))
        assert path == store.user_dir(DummyProfile) / f"{name}.json"
        assert store.path(DummyProfile, name) == (path, True)


class TestProfileDocuments:
    def test_missing_profile_names_kind_and_path(self, store):
        path, exists = store.path(DummyProfile, "missing")
        assert not exists
        assert path == store.user_dir(DummyProfile) / "missing.json"
        with pytest.raises(FileNotFoundError, match=r"No dummy profile for 'missing'.*missing\.json"):
            store.load(DummyProfile, "missing")

    def test_packaged_profile_is_found(self, store):
        packaged = write_packaged(store, "model-a", DummyProfile(model="a", max_power_w=1.0))
        assert store.path(DummyProfile, "model-a") == (packaged, True)
        assert store.load(DummyProfile, "model-a").model == "a"

    def test_user_profile_overrides_packaged_profile(self, store):
        write_packaged(store, "model-a", DummyProfile(model="packaged", max_power_w=1.0))
        user = store.save("model-a", DummyProfile(model="user", max_power_w=2.0))
        assert store.path(DummyProfile, "model-a") == (user, True)
        assert store.load(DummyProfile, "model-a").model == "user"

    def test_invalid_document_raises(self, store):
        path = store.user_dir(DummyProfile) / "broken.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"model": "x"}', encoding="utf-8")
        with pytest.raises(ValidationError):
            store.load(DummyProfile, "broken")


class TestCompiledCache:
    def test_compiled_profile_is_cached(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        first = store.get(DummyProfile, "model-a")
        assert first == {"model": "a", "power_w": 100.0}
        assert store.get(DummyProfile, "model-a") is first

    def test_options_are_passed_and_part_of_cache_key(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        default = store.get(DummyProfile, "model-a")
        scaled = store.get(DummyProfile, "model-a", scale=0.5)
        assert scaled["power_w"] == pytest.approx(50.0)
        assert store.get(DummyProfile, "model-a", scale=0.5) is scaled
        assert store.get(DummyProfile, "model-a") is default
        # Passing the default value explicitly is the same compilation.
        assert store.get(DummyProfile, "model-a", scale=1.0) is default

    def test_changed_file_is_recompiled(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        before = store.get(DummyProfile, "model-a")
        store.save("model-a", DummyProfile(model="a", max_power_w=2500.0))
        after = store.get(DummyProfile, "model-a")
        assert after is not before
        assert after["power_w"] == pytest.approx(2500.0)

    def test_file_edited_outside_eos_applies_after_the_recheck_interval(self, store, monkeypatch):
        recheck_s = 0.2
        # cachebox expires with its own clock: use a short interval and wait.
        monkeypatch.setattr(store, "_recent", cachebox.TTLCache(16, recheck_s))
        path = store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        before = store.get(DummyProfile, "model-a")
        path.write_text(
            DummyProfile(model="a", max_power_w=2500.0).model_dump_json(), encoding="utf-8"
        )
        os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
        # Checked recently: the file is not looked at again.
        assert store.get(DummyProfile, "model-a") is before
        time.sleep(recheck_s * 1.5)
        assert store.get(DummyProfile, "model-a")["power_w"] == pytest.approx(2500.0)

    def test_saved_override_of_a_packaged_profile_applies_at_once(self, store):
        write_packaged(store, "model-a", DummyProfile(model="packaged", max_power_w=1.0))
        assert store.get(DummyProfile, "model-a")["model"] == "packaged"
        store.save("model-a", DummyProfile(model="user", max_power_w=2.0))
        assert store.get(DummyProfile, "model-a")["model"] == "user"

    def test_clear_cache_forces_recompile(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        before = store.get(DummyProfile, "model-a")
        store.clear_cache()
        assert store.get(DummyProfile, "model-a") is not before

    def test_clear_cache_of_one_type_keeps_others(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        store.save("model-a", OtherProfile(model="a"))
        dummy = store.get(DummyProfile, "model-a")
        store.get(OtherProfile, "model-a")
        store.clear_cache(OtherProfile)
        assert store.get(DummyProfile, "model-a") is dummy
        assert [key.document_type for key in store._compiled] == [DummyProfile]


class TestListAndDelete:
    def test_list_shows_user_and_packaged_profiles(self, store):
        write_packaged(store, "model-a", DummyProfile(model="packaged", max_power_w=1.0))
        write_packaged(store, "model-b", DummyProfile(model="packaged", max_power_w=1.0))
        store.save("model-a", DummyProfile(model="user", max_power_w=2.0))
        store.save("model-c", DummyProfile(model="user", max_power_w=3.0))
        # Files that are no valid profile, e.g. in a subdirectory, are not listed.
        (store.user_dir(DummyProfile) / "vendor").mkdir()
        (store.user_dir(DummyProfile) / "vendor" / "model-d.json").write_text("{}", encoding="utf-8")
        (store.user_dir(DummyProfile) / "schema.json").write_text("{}", encoding="utf-8")
        assert store.list_profiles(DummyProfile) == [
            ProfileEntry(name="model-a", source="user", packaged=True),
            ProfileEntry(name="model-b", source="package", packaged=True),
            ProfileEntry(name="model-c", source="user", packaged=False),
        ]

    def test_list_of_a_kind_without_profiles_is_empty(self, store):
        assert store.list_profiles(OtherProfile) == []

    def test_delete_user_profile_makes_the_packaged_one_apply(self, store):
        write_packaged(store, "model-a", DummyProfile(model="packaged", max_power_w=1.0))
        store.save("model-a", DummyProfile(model="user", max_power_w=2.0))
        assert store.get(DummyProfile, "model-a")["model"] == "user"
        store.delete(DummyProfile, "model-a")
        assert store.get(DummyProfile, "model-a")["model"] == "packaged"
        assert store.list_profiles(DummyProfile) == [
            ProfileEntry(name="model-a", source="package", packaged=True)
        ]

    def test_packaged_profile_cannot_be_deleted(self, store):
        packaged = write_packaged(store, "model-a", DummyProfile(model="a", max_power_w=1.0))
        with pytest.raises(PermissionError, match="packaged"):
            store.delete(DummyProfile, "model-a")
        assert packaged.is_file()

    def test_delete_of_a_missing_profile_raises(self, store):
        with pytest.raises(FileNotFoundError, match="No dummy profile for 'missing'"):
            store.delete(DummyProfile, "missing")

    def test_delete_rejects_unsafe_names(self, store):
        with pytest.raises(ValueError, match="Invalid dummy profile name"):
            store.delete(DummyProfile, "../escape")

    def test_delete_refuses_a_directory(self, store):
        """A directory named like a profile file is no profile: ValueError, not OSError."""
        (store.user_dir(DummyProfile) / "model-a.json").mkdir(parents=True)
        with pytest.raises(ValueError, match="is not a file"):
            store.delete(DummyProfile, "model-a")


class TestConcurrency:
    def test_save_replaces_atomically_and_leaves_no_temp_files(self, store):
        path = store.save("model-a", DummyProfile(model="a", max_power_w=1.0))
        store.save("model-a", DummyProfile(model="b", max_power_w=2.0))
        assert [p.name for p in path.parent.iterdir()] == ["model-a.json"]

    def test_failed_save_keeps_the_old_document(self, store, monkeypatch):
        import os

        path = store.save("model-a", DummyProfile(model="old", max_power_w=1.0))

        def broken_replace(src: Any, dst: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="disk full"):
            store.save("model-a", DummyProfile(model="new", max_power_w=2.0))
        assert store.load(DummyProfile, "model-a").model == "old"
        assert [p.name for p in path.parent.iterdir()] == ["model-a.json"]

    def test_save_drops_the_cache_of_the_file(self, store, monkeypatch):
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        store.get(DummyProfile, "model-a")
        assert store._compiled
        store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        assert store._compiled == {}

    def test_save_during_a_lookup_is_not_hidden_by_the_cache(self, store, monkeypatch):
        """A lookup that read the old file must not cache it after a concurrent save.

        The saved file has the same size and, as on file systems with coarse
        timestamps, the same modification time as the old one.
        """
        path = store.save("model-a", DummyProfile(model="a", max_power_w=100.0))
        old_mtime_ns = path.stat().st_mtime_ns
        original_load = store.load_file
        saved: list[bool] = []

        def load_then_save(document_type, file_path):
            document = original_load(document_type, file_path)
            if not saved:
                saved.append(True)
                store.save("model-a", DummyProfile(model="a", max_power_w=200.0))
                os.utime(path, ns=(old_mtime_ns, old_mtime_ns))
            return document

        monkeypatch.setattr(store, "load_file", load_then_save)
        assert store.get(DummyProfile, "model-a")["power_w"] == pytest.approx(100.0)
        assert store.get(DummyProfile, "model-a")["power_w"] == pytest.approx(200.0)

    def test_readers_never_see_a_partial_document(self, store):
        store.save("model-a", DummyProfile(model="a", max_power_w=0.0))
        errors: list[BaseException] = []
        stop = threading.Event()

        def write() -> None:
            try:
                for i in range(200):
                    store.save("model-a", DummyProfile(model="a" * (i % 50 + 1), max_power_w=i))
            except BaseException as exc:  # noqa: BLE001 - collect for the assertion
                errors.append(exc)
            finally:
                stop.set()

        def read() -> None:
            while not stop.is_set():
                try:
                    store.get(DummyProfile, "model-a")
                except BaseException as exc:  # noqa: BLE001 - collect for the assertion
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=write)] + [
            threading.Thread(target=read) for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        assert store.get(DummyProfile, "model-a")["power_w"] == 199.0


class TestSharingViolation:
    """On Windows, replacing or reading a file fails while another handle has it open."""

    @pytest.fixture
    def windows(self, monkeypatch):
        monkeypatch.setattr(profilestore_module, "_RETRY_SHARING_VIOLATIONS", True)
        monkeypatch.setattr(profilestore_module, "SHARING_VIOLATION_RETRY_DELAY_S", 0.0)

    @staticmethod
    def failing_first(original, failures: int):
        calls: list[int] = []

        def operation(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) <= failures:
                raise PermissionError("[WinError 5] Access denied")
            return original(*args, **kwargs)

        return operation

    def test_save_retries_a_replace_blocked_by_a_reader(self, store, windows, monkeypatch):
        store.save("model-a", DummyProfile(model="old", max_power_w=1.0))
        monkeypatch.setattr(os, "replace", self.failing_first(os.replace, 3))
        path = store.save("model-a", DummyProfile(model="new", max_power_w=2.0))
        assert store.load(DummyProfile, "model-a").model == "new"
        assert [p.name for p in path.parent.iterdir()] == ["model-a.json"]

    def test_load_retries_a_read_blocked_by_a_replace(self, store, windows, monkeypatch):
        store.save("model-a", DummyProfile(model="a", max_power_w=1.0))
        monkeypatch.setattr(Path, "read_text", self.failing_first(Path.read_text, 3))
        assert store.load(DummyProfile, "model-a").model == "a"

    def test_lasting_permission_error_is_raised(self, store, windows, monkeypatch):
        store.save("model-a", DummyProfile(model="a", max_power_w=1.0))
        monkeypatch.setattr(Path, "read_text", self.failing_first(Path.read_text, 10**6))
        with pytest.raises(PermissionError):
            store.load(DummyProfile, "model-a")

    def test_permission_error_is_not_retried_elsewhere(self, store, monkeypatch):
        monkeypatch.setattr(profilestore_module, "_RETRY_SHARING_VIOLATIONS", False)
        store.save("model-a", DummyProfile(model="a", max_power_w=1.0))
        read_text = self.failing_first(Path.read_text, 1)
        monkeypatch.setattr(Path, "read_text", read_text)
        with pytest.raises(PermissionError):
            store.load(DummyProfile, "model-a")
