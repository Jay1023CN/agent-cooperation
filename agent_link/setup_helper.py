"""Loopback-only setup helper for the workbench on this computer."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agent_link.device_setup import inspect_device


def validate_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid workbench origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Workbench origin must be HTTPS or loopback HTTP, without a path")
    return origin


def create_app(
    origins: list[str], expected_tailnet: str | None = None, center: str | None = None, center_url: str | None = None
):
    from agent_link.device_setup import _center_url, _name

    _, center_host = _center_url(center_url)
    if center_host and center and center_host != _name(center):
        raise ValueError("Center URL and device address must match")
    allowed = {validate_origin(origin) for origin in origins}
    if not allowed:
        raise ValueError("At least one workbench origin is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            parsed_host = urlsplit("http://" + host)
            host_ok = (
                parsed_host.hostname in {"localhost", "127.0.0.1", "::1"}
                and parsed_host.username is None
                and parsed_host.password is None
                and not parsed_host.path
                and not parsed_host.query
                and not parsed_host.fragment
            )
            _ = parsed_host.port
        except ValueError:
            host_ok = False
        origin = request.headers.get("origin")
        if not host_ok or origin not in allowed:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        if request.method == "OPTIONS":
            if request.headers.get("access-control-request-method") != "GET":
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "GET"
            if request.headers.get("access-control-request-private-network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        else:
            response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/v1/status")
    def status():
        return inspect_device(expected_tailnet, center, center_url)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Cooperation device setup helper")
    parser.add_argument("--origin", action="append", help="Exact workbench origin allowed to read this device")
    parser.add_argument("--expected-tailnet", help="Tailnet name recorded by the project owner")
    parser.add_argument("--center", help="Center device's Tailscale DNS name or IP")
    parser.add_argument("--center-url", help="HTTPS origin of the Agent Cooperation center")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--status", action="store_true", help="Print this computer's status and exit")
    args = parser.parse_args()
    if args.status:
        print(
            json.dumps(
                inspect_device(args.expected_tailnet, args.center, args.center_url), ensure_ascii=False, indent=2
            )
        )
        return
    if not args.origin:
        parser.error("--origin is required when serving")
    import uvicorn

    uvicorn.run(
        create_app(args.origin, args.expected_tailnet, args.center, args.center_url), host="127.0.0.1", port=args.port
    )


if __name__ == "__main__":
    main()
