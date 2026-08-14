# Sanay3i Bridge — Catalog ↔ Engine Contract

The bridge lets any local frontend (the Parts Studio catalog, or any tool
that emits CommandFile JSON) drive the Sanay3i engine through the exact
validate → preview → **confirm** → execute pipeline the CLI enforces.

Start it:

```bash
swpilot bridge                # 127.0.0.1:8765, mock backend
swpilot bridge --port 9000 --backend solidworks   # on a SolidWorks machine
```

It binds to **127.0.0.1 only** and rejects non-localhost origins. Request
bodies are capped at 1 MiB.

## The two-step flow (the confirmation gate)

Nothing executes from a single request. The catalog first *previews*, shows
the user the parsed command list, and only executes after the user confirms:

### 1. `POST /v1/commandfile` — validate & preview

Body: a CommandFile — the JSON the catalog already emits:

```json
{
  "schema_version": "0.5",
  "commands": [
    { "op": "create_plate", "width": 100, "height": 50, "thickness": 10 },
    { "op": "save_part", "path": "plate.SLDPRT" }
  ]
}
```

Responses:

- `200` — valid. Returns the preview and a **one-time token** (expires in
  10 minutes):

  ```json
  {
    "token": "…",
    "commands": [ { "op": "create_plate", "params": { "width": 100.0, … } }, … ],
    "expanded_count": 5
  }
  ```

- `422` — the file failed validation (unknown op, bad params, unsafe save
  path, oversized counts …). The `error` field carries the same message the
  CLI would print.
- `400` malformed JSON · `413` body too large · `403` non-local origin.

### 2. `POST /v1/execute` — the user confirmed

```json
{ "token": "…" }
```

- `200` — executed; returns the full run report:
  `{ "report": { "success": true, "backend": "mock", "results": [...] } }`
- `404` — unknown, expired, or **already-used** token (tokens are single
  use; re-preview to retry).

### `GET /v1/health`

`{ "product": "Sanay3i (صنايعي)", "version": "…", "schema_version": "0.5", "backend": "mock" }`

## Minimal catalog-side example

```js
const base = "http://127.0.0.1:8765";

// 1. preview
const prev = await fetch(base + "/v1/commandfile", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(commandFile),
}).then(r => r.json());

// 2. show prev.commands to the user; on their explicit confirmation:
const run = await fetch(base + "/v1/execute", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ token: prev.token }),
}).then(r => r.json());

console.log(run.report.success);
```

The engine is not coupled to any frontend: anything that speaks this
two-call contract can drive it locally.
