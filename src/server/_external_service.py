"""Shared client-side helper for calling optional external services
(the semantic-mask service, ACE-Step, and whatever comes next).

This is the one place this pattern is allowed to repeat across services --
sharing it doesn't cross the isolation boundary between the actually-
separate services (each still owns its own process, dependencies, and
Dockerfile), because this module lives entirely inside server/, the single
codebase that calls all of them. See docs/DESIGN.md sec 3.3 for why the
services themselves are never allowed to share code with each other, only
this client-side layer is.

Every optional service follows the same contract: configured via one env
var holding its base URL; absent/unreachable degrades to a clean 503
(never a 500, never blocks startup); checked at request time, every time.
"""
import os

import httpx
from fastapi import HTTPException


def service_url(env_var: str) -> str | None:
    return os.environ.get(env_var)


async def service_available(env_var: str, health_path: str = "/health", timeout: float = 2.0) -> bool:
    """Non-raising variant for /capabilities-style boolean checks."""
    url = service_url(env_var)
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}{health_path}")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def call_optional_service(
    env_var: str,
    method: str,
    path: str,
    *,
    service_name: str,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """Call `method path` against the service configured by `env_var`.

    Raises HTTPException(503) with a clear message if the env var is unset
    or the request fails/times out/errors -- the standard degrade-cleanly
    contract. Safe to call from a background task too (not just a route
    handler): HTTPException is just a normal exception outside the request
    cycle, catch it and read `.detail` for a ready-made error message.
    """
    url = service_url(env_var)
    if not url:
        raise HTTPException(503, f"{service_name} is unavailable: no {env_var} configured.")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{url}{path}", **kwargs)
            resp.raise_for_status()
            return resp
    except httpx.HTTPError:
        raise HTTPException(503, f"{service_name} is unavailable: unreachable or returned an error.")
