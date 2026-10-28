"""Reserve a lineage with a single remote coordinator before local handoff."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx


class CoordinationError(RuntimeError):
    pass


class CoordinationClient:
    def __init__(self, url: str, token: str, *, transport: httpx.BaseTransport | None = None):
        parsed = urlsplit(url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CoordinationError("Invalid coordinator URL")
        if parsed.scheme == "http":
            try:
                local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                local = False
            if not local:
                raise CoordinationError("Non-loopback coordinator requires HTTPS")
        elif parsed.scheme != "https":
            raise CoordinationError("Coordinator requires HTTPS or loopback HTTP")
        if not isinstance(token, str) or len(token) < 32:
            raise CoordinationError("Missing coordinator machine token")
        self.url, self.token, self.transport = url.rstrip("/"), token, transport

    def reserve(self, workspace_id: str, lineage: str, task_id: str, instance_id: str) -> None:
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = client.post(
                    self.url + "/v1/reservations",
                    headers={"Authorization": "Bearer " + self.token},
                    json={
                        "workspace_id": workspace_id,
                        "lineage": lineage,
                        "task_id": task_id,
                        "instance_id": instance_id,
                    },
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "reserved":
                raise CoordinationError("Coordinator returned an invalid reservation")
        except (httpx.HTTPError, ValueError):
            raise CoordinationError(
                "Coordinator reservation failed or outcome is unknown; inspect before retrying"
            ) from None

    def inspect(self, workspace_id: str, lineage: str) -> dict:
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = client.post(
                    self.url + "/v1/reservations/inspect",
                    headers={"Authorization": "Bearer " + self.token},
                    json={"workspace_id": workspace_id, "lineage": lineage},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") not in ("reserved", "missing"):
                raise CoordinationError("Coordinator returned an invalid inspection result")
            return body
        except (httpx.HTTPError, ValueError):
            raise CoordinationError("Unable to inspect coordinator reservation") from None

    def check(self, workspace_id: str, lineage: str, task_id: str, instance_id: str) -> None:
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = client.post(
                    self.url + "/v1/reservations/check",
                    headers={"Authorization": "Bearer " + self.token},
                    json={
                        "workspace_id": workspace_id,
                        "lineage": lineage,
                        "task_id": task_id,
                        "instance_id": instance_id,
                    },
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "reserved":
                raise CoordinationError("Coordinator returned an invalid reservation check")
        except (httpx.HTTPError, ValueError):
            raise CoordinationError("Unable to verify coordinator reservation; lease will not be renewed") from None
