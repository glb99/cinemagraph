"""Shared client-side helper for calling optional external services
(the semantic-mask service, ACE-Step, and whatever comes next).

This is the one place this pattern is allowed to repeat across services --
sharing it doesn't cross the isolation boundary between the actually-
separate services (each still owns its own process, dependencies, and
Dockerfile), because this module lives entirely inside server/, the single
codebase that calls all of them. See docs/DESIGN.md sec 3.3 for why the
services themselves are never allowed to share code with each other, only
this client-side layer is.

Every optional service follows the same contract: configured by one env var
holding its base URL; absent/unreachable degrades to a clean 503 (never a
500, never blocks startup). This module now knows nothing about the
environment at all -- it takes an already-resolved config.OptionalService
and is pure transport, so *reachability* is still checked on every call
while *configuration* is resolved once by config.get_settings().
"""

from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from .config import OptionalService


@dataclass(frozen=True)
class ServiceProbe:
    """The full result of one health check, rather than just a bool.

    `configured` and `reachable` are deliberately separate: "no URL set" and
    "URL set but nothing answering" look identical to a caller that only sees
    False, and they need different advice -- one is "enable this feature", the
    other is "your container isn't running".

    `health` is the service's own /health body when it answered, which since
    the busy-watchdog work carries `model_loaded` and `inflight_seconds`. That
    lets the UI say "loaded and idle" or "wedged" instead of just "up".
    """

    configured: bool
    reachable: bool
    health: dict | None = None


async def probe_service(
    service: OptionalService, health_path: str = "/health", timeout: float = 2.0
) -> ServiceProbe:
    if not service.url:
        return ServiceProbe(configured=False, reachable=False)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{service.url}{health_path}")
    except httpx.HTTPError:
        return ServiceProbe(configured=True, reachable=False)

    body: dict | None
    try:
        parsed = resp.json()
        body = parsed if isinstance(parsed, dict) else None
    except ValueError:
        # A service answering /health with something that isn't JSON is still
        # reachable; the detail is just unavailable.
        body = None
    # A 503 here is the satellites' own "wedged" answer, so reachable=False is
    # the right call for the capability bool -- it genuinely can't serve a
    # request -- while `health` still carries the reason for the UI to show.
    return ServiceProbe(configured=True, reachable=resp.status_code == 200, health=body)


async def service_available(
    service: OptionalService, health_path: str = "/health", timeout: float = 2.0
) -> bool:
    """Non-raising variant for /capabilities-style boolean checks.

    Thin wrapper over probe_service so there is one implementation of "is this
    thing up", not two that can drift.
    """
    return (await probe_service(service, health_path, timeout)).reachable


async def call_optional_service(
    service: OptionalService,
    method: str,
    path: str,
    *,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """Call `method path` against `service`.

    Raises HTTPException(503) with a clear message if the service isn't
    configured or the request fails/times out/errors -- the standard
    degrade-cleanly contract. Safe to call from a background task too (not
    just a route handler): HTTPException is just a normal exception outside
    the request cycle, catch it and read `.detail` for a ready-made message.
    """
    if not service.url:
        raise HTTPException(
            503, f"{service.name} is unavailable: no {service.env_var} configured."
        )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{service.url}{path}", **kwargs)
            resp.raise_for_status()
            return resp
    except httpx.HTTPError:
        raise HTTPException(
            503, f"{service.name} is unavailable: unreachable or returned an error."
        )
