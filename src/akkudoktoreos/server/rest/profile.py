"""REST API of the device profile store.

Lists, reads, writes and deletes device profile documents (e.g. battery
profiles) for clients such as EOSdash. The profile kind in the path selects the
profile type (see ``profilestore.profile_type``)::

    GET    /v1/profile                       registered profile kinds
    GET    /v1/profile/{kind}                profiles of a kind
    GET    /v1/profile/{kind}/schema         JSON schema of the documents of a kind
    GET    /v1/profile/{kind}/{name}         document of a profile
    PUT    /v1/profile/{kind}/{name}         validate and save a document (user data directory)
    DELETE /v1/profile/{kind}/{name}         delete a user profile

A profile name is a single file name stem: letters, digits, ``.``, ``_`` and
``-``, starting with a letter or digit (``profilestore.PROFILE_NAME_PATTERN``);
``schema`` is reserved, as that path serves the JSON schema. Packaged profiles
are read only: a PUT stores a user profile that overrides the packaged one of
the same name, a DELETE of that user profile makes the packaged one apply again.

Saved profiles take effect at once: the store drops the compiled profile, and
users of the profile (battery settings, device controllers) get the new one on
their next access.
"""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi import Path as FastapiPath

# Import the profile types so that they are registered with the store.
from akkudoktoreos.devices.profile import batteryprofile  # noqa: F401
from akkudoktoreos.devices.profile.profilestore import (
    ProfileDocument,
    ProfileEntry,
    get_profile_store,
    profile_kinds,
    profile_type,
)
from akkudoktoreos.server.rest.error import EOSProblem

router = APIRouter(prefix="/v1/profile", tags=["profile"])

KindPath = Annotated[str, FastapiPath(description="Profile kind, e.g. 'battery'.")]
NamePath = Annotated[
    str,
    FastapiPath(
        description="Profile name without '.json', e.g. 'generic-lfp' (letters, digits, '.', '_', '-')."
    ),
]


def _document_type(kind: str) -> type[ProfileDocument[Any]]:
    """Return the profile type of a kind, or raise 404."""
    try:
        return profile_type(kind)
    except KeyError as exc:
        raise EOSProblem(
            status=404, title="Unknown profile kind", detail=f"Unknown profile kind '{kind}'."
        ) from exc


@router.get("", response_model=list[str])
async def get_profile_kinds() -> list[str]:
    """Return the registered profile kinds."""
    return profile_kinds()


@router.get("/{kind}", response_model=list[ProfileEntry])
async def get_profiles(kind: KindPath) -> list[ProfileEntry]:
    """Return the profiles of a kind, from the user and the packaged data directory."""
    document_type = _document_type(kind)
    return await asyncio.to_thread(get_profile_store().list_profiles, document_type)


@router.get("/{kind}/schema")
async def get_profile_schema(kind: KindPath) -> dict[str, Any]:
    """Return the JSON schema of the profile documents of a kind."""
    return _document_type(kind).model_json_schema()


@router.get("/{kind}/{name}")
async def get_profile(kind: KindPath, name: NamePath) -> dict[str, Any]:
    """Return the document of a profile (user profile first, then packaged)."""
    document_type = _document_type(kind)
    try:
        document = await asyncio.to_thread(get_profile_store().load, document_type, name)
    except FileNotFoundError as exc:
        raise EOSProblem(status=404, title="Profile not found", detail=str(exc)) from exc
    except ValueError as exc:  # invalid name or invalid document on disk
        raise EOSProblem(status=422, title="Profile retrieval failed", detail=str(exc)) from exc
    return document.model_dump(mode="json")


@router.put("/{kind}/{name}")
async def put_profile(
    kind: KindPath,
    name: NamePath,
    document: Annotated[dict[str, Any], Body(description="The profile document.")],
) -> dict[str, Any]:
    """Validate a profile document and save it to the user data directory.

    Returns the saved (validated, defaults filled in) document.
    """
    document_type = _document_type(kind)
    try:
        validated = document_type.model_validate(document)
        await asyncio.to_thread(get_profile_store().save, name, validated)
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        raise EOSProblem(status=422, title="Profile save failed", detail=str(exc)) from exc
    except OSError as exc:
        raise EOSProblem(
            status=500,
            title="Profile save failed",
            detail=f"Cannot save {kind} profile '{name}': {exc}",
            cause=exc,
        ) from exc
    return validated.model_dump(mode="json")


@router.delete("/{kind}/{name}", response_model=ProfileEntry | None)
async def delete_profile(kind: KindPath, name: NamePath) -> ProfileEntry | None:
    """Delete a user profile.

    Returns the profile that applies now (the packaged one of the same name),
    or null if there is none.
    """
    document_type = _document_type(kind)
    store = get_profile_store()
    try:
        await asyncio.to_thread(store.delete, document_type, name)
    except PermissionError as exc:
        raise EOSProblem(status=409, title="Profile delete refused", detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise EOSProblem(status=404, title="Profile not found", detail=str(exc)) from exc
    except ValueError as exc:
        raise EOSProblem(status=422, title="Profile delete failed", detail=str(exc)) from exc
    entries = await asyncio.to_thread(store.list_profiles, document_type)
    return next((entry for entry in entries if entry.name == name), None)
