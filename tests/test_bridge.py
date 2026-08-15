# ============================================================
# Sanay3i (صنايعي) — AI-Powered Mechanical CAD Automation
# Copyright (c) 2026 Eng. Osama Isa Ali Alassar. All Rights Reserved.
# Proprietary and confidential. Unauthorized copying, use, or
# distribution of this file, via any medium, is strictly prohibited.
# Product: Sanay3i (صنايعي)  |  Owner: Eng. Osama Isa Ali Alassar
# ============================================================

"""Catalog↔engine bridge tests (v1.2), against the mock backend in CI.

The bridge must be exactly the existing pipeline behind two localhost
endpoints: validation identical to the CLI, and the confirmation gate as a
mandatory second call with a one-time token. Nothing may execute from a
single request.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from swpilot.bridge import MAX_BODY_BYTES, BridgeError, BridgeState, create_server

GOOD = {
    "schema_version": "0.5",
    "commands": [
        {"op": "create_plate", "width": 100, "height": 50, "thickness": 10},
        {"op": "save_part", "path": "plate.SLDPRT"},
    ],
}


@pytest.fixture(scope="module")
def bridge_url() -> Iterator[str]:
    server = create_server(port=0)  # ephemeral port, mock backend
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _post(
    url: str, payload: object, origin: str | None = None
) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestHealthAndValidation:
    def test_health(self, bridge_url: str) -> None:
        status, data = _get(bridge_url + "/v1/health")
        assert status == 200
        assert data["backend"] == "mock"
        assert data["schema_version"] == "0.5"
        assert "Sanay3i" in str(data["product"])

    def test_good_commandfile_previews(self, bridge_url: str) -> None:
        status, data = _post(bridge_url + "/v1/commandfile", GOOD)
        assert status == 200
        assert [c["op"] for c in data["commands"]] == ["create_plate", "save_part"]  # type: ignore[index,union-attr]
        assert data["expanded_count"] == 5
        assert isinstance(data["token"], str) and len(data["token"]) > 20  # type: ignore[arg-type]

    def test_invalid_commandfile_rejected_with_cli_error(self, bridge_url: str) -> None:
        bad = {"schema_version": "0.5", "commands": [{"op": "sweep"}]}
        status, data = _post(bridge_url + "/v1/commandfile", bad)
        assert status == 422
        assert "invalid command file" in str(data["error"])

    def test_unsafe_save_path_rejected_same_as_cli(self, bridge_url: str) -> None:
        evil = {
            "schema_version": "0.5",
            "commands": [
                {"op": "create_plate", "width": 10, "height": 10, "thickness": 2},
                {"op": "save_part", "path": "../evil.SLDPRT"},
            ],
        }
        status, data = _post(bridge_url + "/v1/commandfile", evil)
        assert status == 422
        assert "traversal" in str(data["error"])

    def test_malformed_json_rejected(self, bridge_url: str) -> None:
        req = urllib.request.Request(
            bridge_url + "/v1/commandfile",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400

    def test_oversized_body_rejected(self, bridge_url: str) -> None:
        big = {"schema_version": "0.5", "commands": [], "pad": "x" * (MAX_BODY_BYTES + 10)}
        status, data = _post(bridge_url + "/v1/commandfile", big)
        assert status == 413

    def test_unknown_endpoint_404(self, bridge_url: str) -> None:
        status, _ = _post(bridge_url + "/v1/nope", {})
        assert status == 404


class TestConfirmationGate:
    def test_nothing_executes_without_the_second_call(self, bridge_url: str) -> None:
        # a preview returns no report and leaves no side effect visible;
        # only /v1/execute with the token runs anything
        status, data = _post(bridge_url + "/v1/commandfile", GOOD)
        assert status == 200
        assert "report" not in data

    def test_preview_then_execute_runs_the_pipeline(self, bridge_url: str) -> None:
        _, prev = _post(bridge_url + "/v1/commandfile", GOOD)
        status, run = _post(bridge_url + "/v1/execute", {"token": prev["token"]})
        assert status == 200
        report = run["report"]
        assert report["success"] is True  # type: ignore[index]
        assert report["backend"] == "mock"  # type: ignore[index]

    def test_token_is_single_use(self, bridge_url: str) -> None:
        _, prev = _post(bridge_url + "/v1/commandfile", GOOD)
        _post(bridge_url + "/v1/execute", {"token": prev["token"]})
        status, data = _post(bridge_url + "/v1/execute", {"token": prev["token"]})
        assert status == 404

    def test_unknown_token_rejected(self, bridge_url: str) -> None:
        status, _ = _post(bridge_url + "/v1/execute", {"token": "forged-token"})
        assert status == 404

    def test_missing_token_rejected(self, bridge_url: str) -> None:
        status, _ = _post(bridge_url + "/v1/execute", {})
        assert status == 400

    def test_expired_token_rejected(self, bridge_url: str) -> None:
        _, prev = _post(bridge_url + "/v1/commandfile", GOOD)
        # force expiry rather than waiting out the TTL
        state: BridgeState
        # reach into the running server's state via a fresh preview + prune
        import swpilot.bridge as br

        # emulate: token far in the past
        # (grab any server the fixture started — state lives on the server obj)
        # simplest deterministic check: expire via take on a synthetic state
        state = BridgeState()
        tok = state.add_pending(None, [])  # type: ignore[arg-type]
        state.pending[tok].expires_at = -1.0
        assert state.take_pending(tok) is None
        assert br.TOKEN_TTL_SECONDS > 0


class TestLocalhostOnly:
    def test_server_binds_loopback_only(self) -> None:
        server = create_server(port=0)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()

    def test_non_local_origin_rejected(self, bridge_url: str) -> None:
        status, data = _post(
            bridge_url + "/v1/commandfile", GOOD, origin="https://evil.example.com"
        )
        assert status == 403

    def test_local_origins_allowed(self, bridge_url: str) -> None:
        for origin in ("http://localhost:5173", "http://127.0.0.1:8000"):
            status, _ = _post(bridge_url + "/v1/commandfile", GOOD, origin=origin)
            assert status == 200

    def test_unknown_backend_refused(self) -> None:
        with pytest.raises(BridgeError):
            create_server(port=0, backend="cloud")
