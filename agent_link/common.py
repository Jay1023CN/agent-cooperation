from __future__ import annotations

import base64
import ipaddress
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class BridgeError(RuntimeError):
    pass


def load_config(path=None):
    path = path or os.getenv("AGENT_LINK_CONFIG")
    if not path:
        return {}
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BridgeError("Configuration must be a JSON object.")
    return data


def workspace_path(path):
    resolved = Path(path).expanduser()
    if not resolved.is_absolute() or not resolved.is_dir():
        raise BridgeError("workspace must be an existing absolute directory.")
    return resolved.resolve()


def local_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise BridgeError("Expected a loopback HTTP(S) URL without embedded credentials.")
    if parsed.hostname != "localhost":
        try:
            if not ipaddress.ip_address(parsed.hostname).is_loopback:
                raise ValueError()
        except ValueError:
            raise BridgeError("Desktop endpoints must use a loopback address.") from None
    return url.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("Redirect refused; credentials are never forwarded to another endpoint.")


class JsonHTTP:
    def __init__(self, url, headers=None, *, local=True, self_signed=False):
        self.url = local_url(url) if local else url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if not local and (parsed.scheme != "https" or parsed.username or parsed.password):
            raise BridgeError("Remote endpoints must use HTTPS without embedded credentials.")
        if self_signed and not local:
            raise BridgeError("Self-signed TLS is only supported for loopback endpoints.")
        self.headers = headers or {}
        handlers = [NoRedirect()]
        if local:
            handlers.append(urllib.request.ProxyHandler({}))
        if self_signed:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def open(self, method, path, body=None, timeout=30):
        if not path.startswith("/") or path.startswith("//"):
            raise BridgeError("Invalid endpoint path.")
        headers = {**self.headers, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # Upstream error bodies can contain credentials. Return only status and path.
            raise BridgeError(f'{method} {path.split("?")[0]} returned HTTP {exc.code}.') from None
        except OSError:
            raise BridgeError(
                f"{method} connection failed or timed out; outcome may be unknown. Do not resend mutations automatically."
            ) from None

    def request(self, method, path, body=None, timeout=30):
        with self.open(method, path, body, timeout) as response:
            if response.status == 204:
                return {}
            raw = response.read()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                raise BridgeError("Endpoint did not return JSON; check the server version and URL.") from None


def basic_auth(password):
    return "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()


def unwrap(value):
    return value.get("data", value) if isinstance(value, dict) else value
