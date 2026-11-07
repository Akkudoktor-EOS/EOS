---
name: rest-api
description: Use when adding or changing EOS REST endpoints (FastAPI, `src/akkudoktoreos/server/eos.py` and `src/akkudoktoreos/server/rest/`). Enforces RFC 7807 problem details via EOSProblem for all errors.
---

# REST API

## When to use

- Adding or changing an endpoint in `server/eos.py` or a router in
  `server/rest/`.
- Changing how an endpoint reports errors.

## Errors

EOS answers errors as RFC 7807 problem details (`application/problem+json`
with `type`, `title`, `status`, `detail`, `instance`), see
`src/akkudoktoreos/server/rest/error.py`.

- Raise `EOSProblem`, **never** FastAPI's `HTTPException`:

  ```python
  from akkudoktoreos.server.rest.error import EOSProblem

  try:
      value = get_config().get_nested_value(path)
  except KeyError as exc:
      raise EOSProblem(status=404, title="Config value not found", detail=str(exc)) from exc
  ```

- `title`: short, stable summary of the kind of problem, the same for all
  occurrences (e.g. "Error on cache clear", "Total load prediction failed"). `detail`:
  the specific message for this occurrence.
- Chain the original exception (`raise ... from exc`).
- Pass `cause=exc` only for server-side failures (5xx, unexpected errors in
  EOS): the handler logs the cause with its traceback. Leave it out for client
  errors (bad input, unknown names) so they do not flood the log.
- Do not catch broad exceptions just to convert them; unexpected exceptions are
  already answered as 500 problem details by the registered handler.
- `HTTPException` is only still handled for errors raised by FastAPI and
  Starlette themselves (e.g. unknown routes).

## Tests

- Tests that build their own app around a router must register the handlers,
  otherwise an `EOSProblem` surfaces as an exception in the test client:

  ```python
  app = FastAPI()
  register_problem_handlers(app)
  app.include_router(router)
  ```

- Check the status code of error responses; check the problem body where the
  `title` or `detail` matters to clients.

## Verify

- `docs/_generated/openapi.md` and `openapi.json` are generated from the
  endpoints; regenerate them (`make gen-docs`) after endpoint changes.
