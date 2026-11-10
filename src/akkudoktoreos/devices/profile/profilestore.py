"""Storage of device profile documents.

A device profile is a JSON document that describes a device model (e.g. a
battery or an inverter model). Each profile type is a ``ProfileDocument``
subclass: it validates the document, names its kind (the profile directory)
and compiles the document into its runtime form. Defining a subclass with a
``profile_kind`` registers it, so the kind can be looked up by name (e.g. from
a REST path), see ``profile_type``.

One store serves all profile types; callers pass the profile type::

    from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings
    from akkudoktoreos.devices.profile.profilestore import get_profile_store

    profile = get_profile_store().get(BatteryProfileSettings, "generic-lfp")

The store handles what is common to all profile types:

- locating documents named ``<name>.json``, searching in order
    1. the user data directory: ``<general.data_folder_path>/profile/<kind>``
    2. the packaged data directory: ``<package_root_path>/data/profile/<kind>``
- listing, loading and validating documents with the profile type,
- saving and deleting documents in the user data directory (packaged profiles
  are never modified; deleting a user profile makes a packaged profile of the
  same name apply again),
- caching the compiled form per profile type, file and compile options. Saving
  or deleting drops the cache entries of the file; a file changed by other
  means is recompiled when its modification time or size changes.

Concurrency: the store is a process-wide singleton and thread safe. Saves are
atomic (a reader sees the old or the new document, never a partial one) and
serialized per file. The methods block on file I/O; async code calls them
through ``asyncio.to_thread``.
"""

import functools
import inspect
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    Hashable,
    Literal,
    NamedTuple,
    Optional,
    TypeVar,
    Union,
)

import cachebox
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import SingletonMixin, get_config

CompiledT = TypeVar("CompiledT")
ResultT = TypeVar("ResultT")

PROFILE_SUFFIX: str = ".json"

# Profile names: one file name stem, the same on every platform (no separators,
# drive letters or "..").
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# Names the REST API uses for other paths below /v1/profile/{kind}/.
RESERVED_PROFILE_NAMES: frozenset[str] = frozenset({"schema"})

# How long get() returns a compiled profile without checking its file again [s].
# Saving or deleting through the store applies at once; an edit of the file
# outside EOS applies after at most this time.
PROFILE_RECHECK_INTERVAL_S: float = 1.0
# Maximum number of recently checked profiles (type, name and options) kept.
PROFILE_RECENT_MAXSIZE: int = 256

# On Windows, replacing a profile file fails while a reader has it open, and a
# read fails while the file is being replaced (PermissionError). Such sharing
# violations last only as long as the other access, so the store retries them.
# Elsewhere a PermissionError is final.
_RETRY_SHARING_VIOLATIONS: bool = os.name == "nt"
# Number of attempts and the wait between them [s] for a sharing violation.
SHARING_VIOLATION_ATTEMPTS: int = 50
SHARING_VIOLATION_RETRY_DELAY_S: float = 0.01

# Profile kinds are directory names, keep them simple and portable.
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")

# Registered profile types by kind, see ProfileDocument.__pydantic_init_subclass__.
_PROFILE_TYPES: dict[str, type["ProfileDocument[Any]"]] = {}
_PROFILE_TYPES_LOCK = threading.Lock()


def _validated_kind(kind: Any, owner: str) -> str:
    """Return ``kind`` if it is a valid profile kind.

    Raises:
        ValueError: If the kind is not lowercase letters and digits.
    """
    if not isinstance(kind, str) or not _KIND_PATTERN.match(kind):
        raise ValueError(
            f"Invalid profile kind {kind!r} of {owner}: use lowercase letters and digits."
        )
    return kind


class ProfileBaseModel(SettingsBaseModel):
    """Base of all profile document models.

    Unknown keys are rejected, so a misspelled key (e.g. ``nominal_capacity_kwh``
    for ``nominal_capacity_wh``) fails validation instead of being ignored.
    """

    model_config = ConfigDict(extra="forbid")


class ProfileDocument(BaseModel, Generic[CompiledT]):
    """Base class of profile documents, parametrized by the compiled form.

    Subclasses set ``profile_kind`` and implement ``compile_profile``::

        class InverterProfileSettings(SettingsBaseModel, ProfileDocument[InverterProfile]):
            profile_kind: ClassVar[str] = "inverter"

            def compile_profile(self, **options: Any) -> InverterProfile: ...

    A subclass that sets ``profile_kind`` itself is registered for that kind.

    Attributes:
        profile_kind: Profile kind, also the name of the profile subdirectory,
            e.g. ``"battery"``. Lowercase letters and digits only.
    """

    profile_kind: ClassVar[str]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Register a subclass that sets its own ``profile_kind``.

        Raises:
            ValueError: If the kind is invalid, or another profile type already
                has this kind.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "profile_kind" not in cls.__dict__:
            return  # e.g. the parametrized ProfileDocument[X] or a subclass of a profile type
        kind = _validated_kind(cls.__dict__["profile_kind"], cls.__name__)
        with _PROFILE_TYPES_LOCK:
            registered = _PROFILE_TYPES.get(kind)
            if registered is not None and (
                registered.__module__,
                registered.__qualname__,
            ) != (cls.__module__, cls.__qualname__):
                raise ValueError(
                    f"Profile kind '{kind}' of {cls.__qualname__} is already used by "
                    f"{registered.__module__}.{registered.__qualname__}."
                )
            # The same class defined again (e.g. a module reload) replaces the old one.
            _PROFILE_TYPES[kind] = cls

    def compile_profile(self, **options: Any) -> CompiledT:
        """Compile this document into its runtime form.

        Keyword options come from ``ProfileStore.get`` and are part of the
        cache key, so they must be hashable.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement compile_profile")


DocumentT = TypeVar("DocumentT", bound=ProfileDocument[Any])


def profile_kind(document_type: type[ProfileDocument[Any]]) -> str:
    """Return the validated profile kind of a profile type.

    Raises:
        ValueError: If the profile type has no kind or an invalid one.
    """
    return _validated_kind(getattr(document_type, "profile_kind", None), document_type.__name__)


def profile_type(kind: str) -> type[ProfileDocument[Any]]:
    """Return the profile type registered for a kind.

    Raises:
        KeyError: If no profile type is registered for the kind.
    """
    with _PROFILE_TYPES_LOCK:
        try:
            return _PROFILE_TYPES[kind]
        except KeyError:
            raise KeyError(f"Unknown profile kind '{kind}'") from None


def profile_kinds() -> list[str]:
    """Return the registered profile kinds, sorted."""
    with _PROFILE_TYPES_LOCK:
        return sorted(_PROFILE_TYPES)


ProfileSource = Literal["user", "package"]


class ProfileEntry(BaseModel):
    """A profile available in the store."""

    name: str = Field(
        json_schema_extra={
            "description": "Profile name, without the '.json' suffix.",
            "examples": ["generic-lfp"],
        }
    )
    source: ProfileSource = Field(
        json_schema_extra={
            "description": (
                "Where the document in effect comes from: 'user' (user data directory, "
                "editable) or 'package' (shipped with EOS, read only)."
            ),
            "examples": ["package"],
        }
    )
    packaged: bool = Field(
        json_schema_extra={
            "description": (
                "Whether a packaged profile of this name exists. Deleting a user profile "
                "that overrides it makes the packaged profile apply again."
            ),
            "examples": [True],
        }
    )


class _CacheKey(NamedTuple):
    """Cache slot of a compiled profile: profile type, file and compile options."""

    document_type: type[ProfileDocument[Any]]
    path: Path
    options: tuple[tuple[str, Hashable], ...]


class _FileState(NamedTuple):
    """State of a profile file the compiled profile was built from."""

    mtime_ns: int
    size: int


@functools.lru_cache(maxsize=None)
def _compile_signature(document_type: type[ProfileDocument[Any]]) -> inspect.Signature:
    """Return the signature of ``compile_profile`` of a profile type (cached)."""
    return inspect.signature(document_type.compile_profile)


def _options_key(
    document_type: type[ProfileDocument[Any]], options: dict[str, Any]
) -> tuple[tuple[str, Hashable], ...]:
    """Return the cache key part of compile options, with defaults filled in.

    Calls that compile the same way share a cache entry, e.g. ``get(T, name)``
    and ``get(T, name, t_step=1.0)`` if ``1.0`` is the default of ``t_step``.

    Raises:
        TypeError: If the options do not fit the signature of ``compile_profile``.
    """
    signature = _compile_signature(document_type)
    bound = signature.bind(None, **options)
    bound.apply_defaults()
    effective: dict[str, Any] = {}
    for parameter_name, value in list(bound.arguments.items())[1:]:  # skip self
        if signature.parameters[parameter_name].kind is inspect.Parameter.VAR_KEYWORD:
            effective.update(value)
        else:
            effective[parameter_name] = value
    return tuple(sorted(effective.items()))


def _profile_names(directory: Path) -> set[str]:
    """Return the names of the profile documents in a directory.

    Files whose stem is no valid profile name are skipped.
    """
    if not directory.is_dir():
        return set()
    return {
        path.stem
        for path in directory.glob(f"*{PROFILE_SUFFIX}")
        if path.is_file()
        and PROFILE_NAME_PATTERN.fullmatch(path.stem)
        and path.stem not in RESERVED_PROFILE_NAMES
    }


def _retry_sharing_violation(operation: Callable[[], ResultT]) -> ResultT:
    """Run a file operation, retrying it while Windows reports a sharing violation.

    Args:
        operation: The file operation, e.g. a read or an ``os.replace``.

    Returns:
        The result of the operation.

    Raises:
        PermissionError: If the operation still fails after all attempts, or at
            once outside Windows.
    """
    attempt = 1
    while True:
        try:
            return operation()
        except PermissionError:
            if not _RETRY_SHARING_VIOLATIONS or attempt >= SHARING_VIOLATION_ATTEMPTS:
                raise
            attempt += 1
            time.sleep(SHARING_VIOLATION_RETRY_DELAY_S)


class ProfileStore(SingletonMixin):
    """Store for profile documents of all profile types (process-wide singleton).

    Every method takes the profile type (a ``ProfileDocument`` subclass) that
    selects the profile directory, the document validation and the compile
    function. Use ``get_profile_store()``.
    """

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._compiled: dict[_CacheKey, tuple[_FileState, Any]] = {}
        # Profiles checked recently: (type, name, options) -> compiled profile,
        # expiring PROFILE_RECHECK_INTERVAL_S after they were checked.
        self._recent: cachebox.TTLCache = cachebox.TTLCache(
            PROFILE_RECENT_MAXSIZE, PROFILE_RECHECK_INTERVAL_S
        )
        # Incremented whenever profiles are saved, deleted or the cache is cleared:
        # a lookup that started before must not record its result as recent.
        self._generation = 0
        # Guards _compiled, _write_locks, _generation and the generation check
        # of _recent; never held for I/O.
        self._cache_lock = threading.Lock()
        self._write_locks: dict[Path, threading.Lock] = {}
        self._initialized = True

    # --- locations ---------------------------------------------------------

    def user_dir(self, document_type: type[ProfileDocument[Any]]) -> Path:
        """Return the user data directory of a profile type (the write location)."""
        return get_config().general.data_folder_path / "profile" / profile_kind(document_type)

    def package_dir(self, document_type: type[ProfileDocument[Any]]) -> Path:
        """Return the packaged data directory of a profile type (read only)."""
        return get_config().package_root_path / "data" / "profile" / profile_kind(document_type)

    @staticmethod
    def _filename(document_type: type[ProfileDocument[Any]], name: str) -> str:
        """Return the file name of a profile, rejecting invalid names.

        A name is a single file name stem: letters, digits, ``.``, ``_`` and
        ``-``, starting with a letter or digit, at most 100 characters. That
        rules out separators, drive letters and ``..`` on every platform.

        Raises:
            ValueError: If the name is invalid or reserved.
        """
        kind = profile_kind(document_type)
        if not PROFILE_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"Invalid {kind} profile name '{name}': use letters, digits, '.', '_' and "
                "'-', starting with a letter or digit (at most 100 characters)."
            )
        if name in RESERVED_PROFILE_NAMES:
            raise ValueError(f"The {kind} profile name '{name}' is reserved.")
        return f"{name}{PROFILE_SUFFIX}"

    def path(self, document_type: type[ProfileDocument[Any]], name: str) -> tuple[Path, bool]:
        """Locate the document of a profile.

        Searches the user data directory first and the packaged data directory
        second.

        Args:
            document_type: Profile type.
            name: Profile name without the ``.json`` suffix.

        Returns:
            A 2-tuple ``(path, exists)``. If the document exists, ``path`` is its
            location; otherwise it is the candidate path in the user data
            directory (the preferred write location).

        Raises:
            ValueError: If the profile kind or the name is invalid.
        """
        filename = self._filename(document_type, name)
        user_dir = self.user_dir(document_type)
        for directory in (user_dir, self.package_dir(document_type)):
            candidate = directory / filename
            if candidate.is_file():
                return candidate, True
        return user_dir / filename, False

    def existing_path(self, document_type: type[ProfileDocument[Any]], name: str) -> Path:
        """Return the path of an existing profile document.

        Raises:
            ValueError: If the profile kind or the name is invalid.
            FileNotFoundError: If no document exists for the name. The message
                names the expected path and the searched directories.
        """
        path, exists = self.path(document_type, name)
        if not exists:
            raise FileNotFoundError(
                f"No {profile_kind(document_type)} profile for '{name}': expected {path}. "
                f"Available profiles are the {PROFILE_SUFFIX} files in {path.parent} "
                f"and {self.package_dir(document_type)}."
            )
        return path

    def list_profiles(self, document_type: type[ProfileDocument[Any]]) -> list[ProfileEntry]:
        """Return the profiles of a profile type, sorted by name.

        A user profile that has the name of a packaged profile overrides it and
        is listed once, with ``source`` ``"user"``.
        """
        user = _profile_names(self.user_dir(document_type))
        package = _profile_names(self.package_dir(document_type))
        return [
            ProfileEntry(
                name=name,
                source="user" if name in user else "package",
                packaged=name in package,
            )
            for name in sorted(user | package)
        ]

    # --- documents -----------------------------------------------------------

    def load_file(self, document_type: type[DocumentT], path: Union[str, Path]) -> DocumentT:
        """Load and validate a profile document from an explicit path.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the document fails validation.
        """
        content = _retry_sharing_violation(lambda: Path(path).read_text(encoding="utf-8"))
        return document_type.model_validate_json(content)

    def load(self, document_type: type[DocumentT], name: str) -> DocumentT:
        """Load and validate the document of a profile by name, not compiled.

        Raises:
            ValueError: If the profile kind or the name is invalid, or the
                document fails validation.
            FileNotFoundError: If no document exists for the name.
        """
        return self.load_file(document_type, self.existing_path(document_type, name))

    def _write_lock(self, path: Path) -> threading.Lock:
        """Return the lock that serializes writes of one profile file."""
        with self._cache_lock:
            return self._write_locks.setdefault(path, threading.Lock())

    def _invalidate(self, path: Path) -> None:
        """Drop the compiled profiles built from a file."""
        with self._cache_lock:
            for key in [key for key in self._compiled if key.path == path]:
                del self._compiled[key]
            # The name may now resolve to another file (e.g. a user profile that
            # overrides a packaged one), so forget all recent checks.
            self._recent.clear()
            self._generation += 1

    def save(self, name: str, document: ProfileDocument[Any]) -> Path:
        """Save a profile document to the user data directory of its type.

        The file is replaced atomically: concurrent readers see the old or the
        new document. On Windows, a replace or a read that collides with the
        other is retried briefly. An existing file with the same name is overwritten and
        missing parent directories are created. The packaged data directory is
        never written.

        Args:
            name: Profile name without the ``.json`` suffix.
            document: The document to save; its type selects the directory.

        Returns:
            The path the document was written to.

        Raises:
            ValueError: If the profile kind or the name is invalid.
            OSError: If the file or its parent directories cannot be written.
        """
        document_type = type(document)
        path = self.user_dir(document_type) / self._filename(document_type, name)
        content = document.model_dump_json(indent=2)
        with self._write_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    tmp.write(content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                _retry_sharing_violation(lambda: os.replace(tmp_name, path))
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
            self._invalidate(path)
        logger.debug("Saved {} profile '{}' to {}", profile_kind(document_type), name, path)
        return path

    def delete(self, document_type: type[ProfileDocument[Any]], name: str) -> Path:
        """Delete a profile document from the user data directory.

        A packaged profile of the same name applies again afterwards.

        Returns:
            The path of the deleted file.

        Raises:
            ValueError: If the profile kind or the name is invalid.
            PermissionError: If only a packaged profile exists for the name.
            FileNotFoundError: If no profile exists for the name.
        """
        filename = self._filename(document_type, name)
        path = self.user_dir(document_type) / filename
        with self._write_lock(path):
            if path.exists() and not path.is_file():
                raise ValueError(
                    f"The {profile_kind(document_type)} profile '{name}' is not a file: {path}."
                )
            try:
                path.unlink()
            except FileNotFoundError:
                if (self.package_dir(document_type) / filename).is_file():
                    raise PermissionError(
                        f"The {profile_kind(document_type)} profile '{name}' is packaged "
                        "with EOS and cannot be deleted."
                    ) from None
                raise FileNotFoundError(
                    f"No {profile_kind(document_type)} profile for '{name}' in {path.parent}."
                ) from None
            self._invalidate(path)
        logger.debug("Deleted {} profile '{}' at {}", profile_kind(document_type), name, path)
        return path

    # --- compiled profiles ---------------------------------------------------

    def get(
        self, document_type: type[ProfileDocument[CompiledT]], name: str, **options: Any
    ) -> CompiledT:
        """Return the compiled form of a profile, cached per type, file and options.

        Args:
            document_type: Profile type.
            name: Profile name without the ``.json`` suffix.
            **options: Keyword options passed to ``compile_profile``. They must
                be hashable; each set of effective options (defaults filled in)
                has its own cache entry.

        Returns:
            The compiled profile.

        Raises:
            ValueError: If the profile kind or the name is invalid, the document
                fails validation or the compile step rejects it.
            FileNotFoundError: If no document exists for the name.
        """
        options_key = _options_key(document_type, options)
        recent_key = (document_type, name, options_key)
        recent: Optional[CompiledT] = self._recent.get(recent_key)
        if recent is not None:
            return recent
        with self._cache_lock:
            generation = self._generation

        path = self.existing_path(document_type, name)
        stat = path.stat()
        state = _FileState(stat.st_mtime_ns, stat.st_size)
        key = _CacheKey(document_type, path, options_key)

        with self._cache_lock:
            cached = self._compiled.get(key)
        if cached is not None and cached[0] == state:
            compiled: CompiledT = cached[1]
        else:
            compiled = self.load_file(document_type, path).compile_profile(**options)
        # Cache only if nothing was saved or deleted meanwhile: this lookup may have
        # read the file before a save, and the saved file may have the same size
        # and, on file systems with coarse timestamps, the same modification time.
        with self._cache_lock:
            if self._generation == generation:
                self._compiled[key] = (state, compiled)
                self._recent[recent_key] = compiled
        return compiled

    def clear_cache(self, document_type: Optional[type[ProfileDocument[Any]]] = None) -> None:
        """Drop compiled profiles, e.g. after the data directories changed.

        Args:
            document_type: Drop only the profiles of this type; all if None.
        """
        with self._cache_lock:
            self._recent.clear()
            self._generation += 1
            if document_type is None:
                self._compiled.clear()
                return
            for key in [key for key in self._compiled if key.document_type is document_type]:
                del self._compiled[key]


def get_profile_store() -> ProfileStore:
    """Return the store of all device profiles (process-wide singleton)."""
    return ProfileStore()
