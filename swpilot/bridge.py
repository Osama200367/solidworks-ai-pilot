# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Catalog ↔ engine bridge: a minimal localhost-only HTTP server.

Lets the Parts Studio catalog (a standalone HTML tool that emits
CommandFile JSON) drive the engine locally, through the EXACT existing
pipeline and safety model:

* ``POST /v1/commandfile`` — validate + macro-expand the JSON (the same
  ``parse_command_data``/``expand_commands`` gate the CLI uses, including
  the ``extra="forbid"`` schema and save-path safety rules). Returns the
  parsed command-list preview and a one-time confirmation token. Nothing
  executes here.
* ``POST /v1/execute`` — present the token to run the previously-previewed
  commands. This is the confirmation gate, uniform for mock and
  solidworks: the catalog UI shows the preview and the user clicks
  confirm. Tokens are random, single-use, and expire.
* ``GET  /v1/health`` — product/version/schema/backend info.

Security posture (stdlib only, no new dependencies):
* binds to 127.0.0.1 only and re-checks the peer address per request;
* rejects non-localhost ``Origin`` headers (CORS echo for localhost only);
* caps the request body at 1 MiB;
* never executes anything that did not pass the full validation gate.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import swpilot
from swpilot.backends.base import Backend, BackendError
from swpilot.commands.loader import (
    CommandFileError,
    ExpandedCommand,
    expand_commands,
    parse_command_data,
)
from swpilot.commands.schema import SCHEMA_VERSION, CommandFile
from swpilot.executor import execute

MAX_BODY_BYTES = 1024 * 1024  # 1 MiB
TOKEN_TTL_SECONDS = 600.0  # a preview must be confirmed within 10 minutes
MAX_PENDING = 256  # cap live previews so un-confirmed ones can't exhaust memory
READ_TIMEOUT_SECONDS = 10  # abort stalled request reads (Content-Length lies)
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
# Exact loopback hosts — an Origin must parse to one of these (never a prefix
# match, which would let http://localhost.evil.com through).
_LOCAL_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_LOCAL_ORIGIN_SCHEMES = frozenset({"http", "https"})


class BridgeError(RuntimeError):
    """Bridge configuration/runtime failure."""


@dataclass
class _Pending:
    command_file: CommandFile
    expanded: list[ExpandedCommand]
    expires_at: float


@dataclass
class BridgeState:
    """Shared server state: the backend choice and pending confirmations."""

    backend: str = "mock"  # "mock" | "solidworks"
    catalog_path: Path | None = None  # HTML served at GET / (same-origin UI)
    pending: dict[str, _Pending] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_pending(self, cf: CommandFile, expanded: list[ExpandedCommand]) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self._prune()
            # Bound live memory: evict the soonest-expiring preview if full.
            while len(self.pending) >= MAX_PENDING:
                oldest = min(self.pending, key=lambda t: self.pending[t].expires_at)
                del self.pending[oldest]
            self.pending[token] = _Pending(cf, expanded, time.monotonic() + TOKEN_TTL_SECONDS)
        return token

    def take_pending(self, token: str) -> _Pending | None:
        """Single use: a token is removed the moment it is presented."""
        with self.lock:
            self._prune()
            return self.pending.pop(token, None)

    def restore_pending(self, token: str, pending: _Pending) -> None:
        """Put a taken token back (e.g. backend failed to start — don't burn it)."""
        with self.lock:
            if pending.expires_at > time.monotonic():
                self.pending[token] = pending

    def _prune(self) -> None:
        now = time.monotonic()
        for tok in [t for t, p in self.pending.items() if p.expires_at < now]:
            del self.pending[tok]


def _make_backend(choice: str) -> Backend:
    if choice == "mock":
        from swpilot.backends.mock.simulator import MockBackend

        return MockBackend()
    try:
        from swpilot.backends.solidworks.com_backend import SolidWorksBackend
    except ImportError as exc:
        raise BridgeError(
            "the 'solidworks' backend requires Windows with pywin32 and "
            "SolidWorks installed; run the bridge with --backend mock elsewhere"
        ) from exc
    return SolidWorksBackend(visible=True, part_template=None)


def _preview(cf: CommandFile, expanded: list[ExpandedCommand]) -> list[dict[str, object]]:
    """The same command list the CLI prints before confirmation."""
    out: list[dict[str, object]] = []
    for c in cf.commands:
        params = {
            k: v for k, v in c.model_dump(exclude={"op"}).items() if v not in (None, [], {})
        }
        out.append({"op": c.op, "params": params})
    return out


class _Handler(BaseHTTPRequestHandler):
    """One request handler; state lives on the server object."""

    server_version = "SanayiBridge/" + swpilot.__version__
    protocol_version = "HTTP/1.1"
    timeout = READ_TIMEOUT_SECONDS  # StreamRequestHandler applies it to the socket

    # -- plumbing ----------------------------------------------------------

    @property
    def _state(self) -> BridgeState:
        state: BridgeState = self.server.bridge_state  # type: ignore[attr-defined]
        return state

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep the bridge quiet; tests and CLI don't want request logs

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # same-machine tools (curl, scripts) send no Origin
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        # Exact host match — never a prefix, so localhost.evil.com is rejected.
        return parts.scheme in _LOCAL_ORIGIN_SCHEMES and parts.hostname in _LOCAL_ORIGIN_HOSTS

    def _send_json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin is not None and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        """Common request guards; True if the request may proceed."""
        if self.client_address[0] not in _LOOPBACK_ADDRS:
            self._send_json(403, {"error": "the bridge only serves localhost"})
            return False
        if not self._origin_ok():
            self._send_json(403, {"error": "non-local origin rejected"})
            return False
        return True

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(411, {"error": "Content-Length required"})
            return None
        if length <= 0:
            self._send_json(411, {"error": "Content-Length required"})
            return None
        if length > MAX_BODY_BYTES:
            # Drain (bounded) so the client can finish sending and read the
            # 413 instead of hitting a broken pipe; beyond the hard cap the
            # connection is closed unread.
            remaining = min(length, 8 * MAX_BODY_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            self.close_connection = True
            self._send_json(413, {"error": f"request body exceeds {MAX_BODY_BYTES} bytes"})
            return None
        return self.rfile.read(length)

    # -- endpoints ---------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        if not self._guard():
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._guard():
            return
        if self.path == "/v1/health":
            self._send_json(
                200,
                {
                    "product": "Sanay3i (صنايعي)",
                    "version": swpilot.__version__,
                    "schema_version": SCHEMA_VERSION,
                    "backend": self._state.backend,
                },
            )
            return
        if self.path in ("/", "/index.html"):
            # Serve the Parts Studio catalog same-origin with the API, so the
            # browser UI needs no CORS at all and file:// pitfalls disappear.
            cat = self._state.catalog_path
            if cat is None or not cat.is_file():
                self._send_json(
                    404,
                    {
                        "error": "catalog not configured",
                        "hint": "start with: swpilot bridge "
                        "--catalog path/to/Sanay3i_Parts_Studio.html",
                    },
                )
                return
            try:
                body = cat.read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": f"catalog read failed: {exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._guard():
            return
        if self.path == "/v1/commandfile":
            self._post_commandfile()
        elif self.path == "/v1/execute":
            self._post_execute()
        else:
            self._send_json(404, {"error": "unknown endpoint"})

    def _post_commandfile(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"malformed JSON: {exc}"})
            return
        try:
            cf = parse_command_data(data)
            expanded = expand_commands(list(cf.commands))
        except CommandFileError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        token = self._state.add_pending(cf, expanded)
        self._send_json(
            200,
            {
                "token": token,
                "commands": _preview(cf, expanded),
                "expanded_count": len(expanded),
                "confirm_with": "POST /v1/execute {\"token\": ...}",
            },
        )

    def _post_execute(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"malformed JSON: {exc}"})
            return
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            self._send_json(400, {"error": "missing 'token'"})
            return
        pending = self._state.take_pending(token)
        if pending is None:
            self._send_json(
                404, {"error": "unknown, expired, or already-used token; re-preview"}
            )
            return
        try:
            backend = _make_backend(self._state.backend)
        except (BridgeError, BackendError) as exc:
            # The backend didn't start (e.g. SolidWorks/COM unavailable). Don't
            # burn the confirmed preview — restore the token so it can retry.
            self._state.restore_pending(token, pending)
            self._send_json(503, {"error": str(exc)})
            return
        try:
            report = execute(
                pending.expanded, backend,
                schema_version=pending.command_file.schema_version,
            )
        except BackendError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        finally:
            backend.close()
        self._send_json(200, {"report": report.to_dict()})


class BridgeServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the bridge state; loopback-only."""

    daemon_threads = True

    def __init__(
        self,
        port: int = 8765,
        backend: str = "mock",
        catalog_path: Path | None = None,
    ) -> None:
        self.bridge_state = BridgeState(backend=backend, catalog_path=catalog_path)
        super().__init__(("127.0.0.1", port), _Handler)


def _autodetect_catalog() -> Path | None:
    """Find the Parts Studio catalog HTML near the package/CWD, if present."""
    candidates: list[Path] = []
    for root in (Path.cwd(), Path(__file__).resolve().parent.parent):
        candidates.extend(sorted(root.glob("Sanay3i_Parts_Studio*.html")))
        candidates.extend(sorted(root.glob("*Parts_Studio*.html")))
    for c in candidates:
        if c.is_file():
            return c
    return None


def create_server(
    port: int = 8765,
    backend: str = "mock",
    catalog: Path | None = None,
) -> BridgeServer:
    """Build the localhost-only bridge server (port 0 = ephemeral, for tests).

    ``catalog`` — path to the Parts Studio HTML served at ``GET /``. When
    omitted, the repo root / CWD is searched for ``Sanay3i_Parts_Studio*.html``.
    """
    if backend not in ("mock", "solidworks"):
        raise BridgeError(f"unknown backend {backend!r}")
    if catalog is not None:
        catalog = Path(catalog)
        if not catalog.is_file():
            raise BridgeError(f"catalog file not found: {catalog}")
    else:
        catalog = _autodetect_catalog()
    return BridgeServer(port=port, backend=backend, catalog_path=catalog)
