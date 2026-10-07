# Research through OpenAlgo MCP

The existing OpenAlgo MCP server exposes the same saved inputs, portfolio jobs,
historical prices and results as the Research screen. It does not run a second
data downloader or backtest service. VectorBT or NautilusTrader performs the
simulation; Optuna searches supported settings. OpenAlgo selects daily or
one-minute prices from the uploaded signals and requested settings, reads
Historify first, and uses the connected broker's normal history adapter for
missing prices. Completed jobs retain their original data and configuration.

## Accounts and permissions

Remote HTTP MCP uses the existing OAuth transport at `/mcp`. Two explicit scopes
are available during consent:

| Scope | Permitted actions |
|---|---|
| `read:research` | Capabilities, saved inputs and runs, preview, results, trades, strategy definition export |
| `write:research` | Save supplied CSV, start, cancel, resume, replay research |

Existing grants retain their original scopes; adding research requires consent
to the new scope. Research permissions do not grant order permissions, and an
order permission does not grant research access. The verified JWT subject must
resolve to an existing native OpenAlgo user. Every source and job is selected
using that exact account. Tool arguments cannot supply an owner or credentials.

The existing stdio `mcp/mcpserver.py API_KEY HOST` integration uses the native
OpenAlgo API key via `POST /api/v1/research/tool`. This endpoint is a restricted
research API bridge, not another MCP transport. The API key identifies its
native account independently of broker login; complete Historify data can be
used when the broker session has expired. The endpoint does not accept a browser
session as authorization. Native API keys retain their existing broad account
authority; `OPENALGO_MCP_READ_ONLY=1` disables research mutations and
`OPENALGO_MCP_TOOLSETS=research` restricts the MCP surface to research. OAuth
clients should request the minimum explicit scopes they need.

No research action submits a trading order, changes trading mode, runs a live
strategy, sends a Telegram notification, or exports credentials. A strategy
definition is a reproducible research configuration, with execution disabled.

## Tools

| Tool | Inputs and result |
|---|---|
| `research_capabilities` | Available engine versions, supported portfolio markets, intervals and limits |
| `research_list_sources` | Saved account inputs, including CSVs awaiting prices; `limit` up to 100, `offset` |
| `research_list_runs` | Saved runs with `query`, `status`, `cursor`; `limit` up to 100 |
| `research_preview_portfolio` | Validate `portfolio` with existing `source_id` values; queues and downloads nothing |
| `research_upload_csv` | Save user-supplied `csv_text`, up to 8 MiB UTF-8; return its source ID |
| `research_run_portfolio` | Queue `portfolio`; required stable `request_id` prevents retry duplication |
| `research_get_run` | Progress or saved summary for `job_id`; at most 300 equity points and 25 trial summaries |
| `research_get_trades` | Saved `job_id` ledger, optional `strategy_id`/`status`; `limit` up to 200 with `offset` |
| `research_cancel_run` | Cancel `job_id` through the existing research worker |
| `research_resume_run` | Resume `job_id` only when its saved checkpoint is resumable |
| `research_rerun_trial` | Replay `job_id` and optional saved `trial_id` using frozen prices; stable `request_id` required |
| `research_export_strategy` | Return exact `strategy_id` settings from a completed `job_id` or its saved `trial_id` |

An assistant must use an uploaded source ID or CSV text the user actually
provided. It must not invent a missing scanner strategy, signal file, or broker
data. The preview uses the same portfolio contract as the browser. For example,
after `research_list_sources` has returned a real ID:

```json
{
  "portfolio": {
    "name": "My scanner",
    "capital": 100000,
    "engine": "vectorbt",
    "strategies": [{
      "id": "scanner-a",
      "name": "Scanner A",
      "source_id": "<source ID returned by OpenAlgo>",
      "allocation_pct": 100,
      "config": {"order_size_pct": 10, "hold_sessions": 5}
    }]
  }
}
```

Use that payload with `research_preview_portfolio`. To start the validated run,
send it to `research_run_portfolio` with a unique request ID of 8–128 characters.
Reuse that same request ID if a timeout makes the submission outcome uncertain.
Inspect the returned job ID with `research_get_run`; trade pages and the app's
saved-run link provide detail. Export or replay uses a saved trial's `config_id`
as `trial_id`; it never launches a fresh optimization or replaces saved prices.

## Runtime and resource checks

`app.py` registers `research_mcp_api_bp` with the native Research store and
exempts only this API-key blueprint from CSRF. The cookie-authenticated Research
blueprint remains CSRF protected. Remote OAuth startup remains opt-in.
API calls are capped at 30 per minute, JSON request bodies at 8 MiB plus 64 KiB
of transport overhead, and returned research data at 1 MiB. Paginate large
ledgers instead of requesting an entire result bundle. JSON escaping can make
a CSV request exceed the transport cap before its decoded CSV reaches 8 MiB;
use the browser upload for such files.

The stdio client closes its HTTP client and streaming response on success,
failure and size rejection, with an explicit 30-second timeout. Native user
lookup sessions close after each request. Per-token quota state expires and is
bounded to 4,096 buckets of at most 1,000 timestamps. Audits retain action,
scope, duration and a parameters hash; the bridge does not log the API key or
CSV text. MCP tool outputs carry the existing untrusted-data envelope, including
uploaded names and other text that must never become assistant instructions.

`test/research/test_portfolio_mcp.py` covers native stored-price calculation,
Optuna trial replay without download, owner isolation, scope separation, API-key
authorization, request bounds and HTTP cleanup. The shared MCP integrity suite
checks that registered tools, scopes, annotations and reference documentation
remain aligned.
