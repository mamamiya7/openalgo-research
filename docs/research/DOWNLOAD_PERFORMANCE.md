# Native research price preparation

The research coordinator uses OpenAlgo's existing history service and connected
broker, with native Historify reads, upserts and readback. The improvements below
are confined to the research layer; broker adapters, Historify schema, engine
calculations and the frontend contract are unchanged.

## Less repeated work

- **Daily cache checks:** combine disjoint required dates into bounded ranges per
  symbol. Each range remains shorter than 300 calendar days. Only required
  symbol/date pairs enter the calculation or its coverage counts.
- **Daily history requests:** combine nearby missing windows separated by at most
  20 trading sessions, subject to the same 300-day range bound. The native adapter
  still handles broker-specific limits. Intervening returned candles are filtered
  out unless required; cached/admitted prices are never overwritten. Minute
  requests retain their existing bounds and interval selection.
- **Historify initialization:** one lazy initialization per acquisition scope,
  including a fresh scope on continuation. Every actual read/write still checks
  the configured archive and uses a short, closed native connection.
- **Evidence publication:** related immutable files share one accounted storage
  admission. Existing files need no scan; a single new file needs one scan;
  multi-file scopes reconcile usage once more before the caller publishes its
  metadata. Ordinary progress checks remain, while a checkpoint skips the
  redundant quota scan immediately before its guarded artifact publication.

Broker calls remain sequential and use existing pacing. The current generic
history limiter is not sufficient to justify unrestricted concurrent requests.
This increment obtains its speedup by reducing calls and local overhead; it adds
no downloader thread pool, second broker client, connection pool or global cache.

## Evidence and recovery

The requested price plan and acquisition input identity remain unchanged by
batching. Exact required OHLC, native timestamps, source attribution and engine
inputs remain unchanged for equal broker data. Receipt hashes and checkpoint
request journals can differ because they truthfully record the larger requests.
Existing saved reports and export bytes are not rewritten.

Completed request windows remain barriers when grouping future work, including
successful empty responses. Resume does not silently refill a previously confirmed
gap or revise an admitted candle. A received response is saved before ingestion;
prices count as downloaded only after native write/readback verification. Existing
time budgets, cancellation, broker errors, queue continuation and storage limits
remain in force.

Artifact admission uses the existing SQL write guard across each synchronous
publication scope. Artifact publishers and maintenance serialize; no new schema or
disk format is introduced. The scope closes before worker progress/checkpoint SQL
transactions. External filesystem writers, including raw receipt files, are not
strictly fenced by that guard. Multi-file reconciliation detects changed usage or
unsafe entries before caller metadata publication; ordinary progress monitoring
still checks the complete store. Failed publications retain only unreferenced
immutable files, which remain accounted for and eligible for existing maintenance.

## Reusing data after a settings change

Price coverage is keyed by symbol, exchange, interval and timestamp, not by job or
strategy settings. Changing target, stop or order size while retaining the same
daily holding window can reuse all matching prices. Increasing a holding window
requires additional dates; changing to timed execution can require minute candles.
New runs check Historify before requesting those differences. Exact replay uses
the original frozen evidence.

Verification covers grouped versus separate windows, cache-only runs, daily and
minute acquisition, holding-period expansion, empty-response continuation,
cancellation before ingestion, failed readback, old checkpoints, quota contention,
maintenance fencing, file failures and resource cleanup. Controlled benchmarks use
temporary native Historify databases and synthetic broker responses; they are not
claims about live broker latency or a guaranteed speed multiplier.

## Controlled timing check

A 16-symbol daily acquisition using real temporary Historify storage and synthetic
broker responses measured 19.51 seconds before these changes and 3.53 seconds
after them. Requests and cache reads both fell from 64 to 16; native initialization
fell from 64 calls to one. A cache-only repeat made no broker requests and measured
5.58 seconds versus 1.62 seconds. Expanding the holding period reused all existing
required candles and wrote only the additional required dates.

The required prices, acquisition identity and coverage counts matched. Broader
requests returned some intervening candles, which were excluded from storage and
calculation when not required. These are single-pass local acquisition timings:
they include native storage and receipt/checkpoint files, but exclude real broker
latency and the production artifact/metadata pipeline. They establish reduced
work, not a promised live download duration.
