"""Problem responses keep server diagnostics private and preserve public errors."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

from akkudoktoreos.server.rest.error import EOSProblem, register_problem_handlers


@pytest.mark.parametrize("api_level", ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"])
@pytest.mark.parametrize("unexpected", [False, True])
def test_problem_responses_keep_diagnostics_in_logs(config_eos, api_level, unexpected):
    config_eos.logging.api_level = api_level
    app = FastAPI()
    register_problem_handlers(app)
    private_detail = "Database failed at /private/server.db with password=secret"

    @app.get("/failure")
    def failure():
        try:
            raise RuntimeError(private_detail)
        except RuntimeError as exc:
            if unexpected:
                raise
            raise EOSProblem(
                status=503,
                title="Service Unavailable",
                detail="The service is temporarily unavailable.",
                type="/problems/service-unavailable",
                cause=exc,
            ) from exc

    logs: list[str] = []
    sink_id = logger.add(logs.append, level="ERROR", backtrace=False, diagnose=False)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/failure?token=private-query-token")
    finally:
        logger.remove(sink_id)

    status = 500 if unexpected else 503
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "/problems/internal-server-error" if unexpected else "/problems/service-unavailable",
        "title": "Internal Server Error" if unexpected else "Service Unavailable",
        "status": status,
        "detail": (
            "An unexpected error occurred."
            if unexpected
            else "The service is temporarily unavailable."
        ),
        "instance": "/failure",
    }
    assert private_detail not in response.text
    assert "private-query-token" not in response.text
    assert any(private_detail in message and "Traceback" in message for message in logs)


def test_http_and_validation_errors_preserve_public_details():
    app = FastAPI()
    register_problem_handlers(app)

    @app.get("/missing")
    def missing():
        raise HTTPException(status_code=404, detail="The requested item does not exist.")

    @app.get("/validate")
    def validate(value: int):
        return {"value": value}

    with TestClient(app) as client:
        missing_response = client.get("/missing")
        validation_response = client.get("/validate?value=private-invalid-input")

    assert missing_response.status_code == 404
    assert missing_response.headers["content-type"] == "application/problem+json"
    assert missing_response.json() == {
        "type": "about:blank",
        "title": "HTTP Error",
        "status": 404,
        "detail": "The requested item does not exist.",
        "instance": "/missing",
    }
    assert validation_response.status_code == 422
    assert validation_response.headers["content-type"] == "application/problem+json"
    assert validation_response.json() == {
        "type": "/problems/validation",
        "title": "Validation Error",
        "status": 422,
        "detail": "Request validation failed.",
        "instance": "/validate",
    }
    assert "private-invalid-input" not in validation_response.text
