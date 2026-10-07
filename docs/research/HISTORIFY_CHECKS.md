# Faster existing-price checks — 30 September 2026

Research reads the matching daily or minute OHLC from OpenAlgo's Historify archive,
downloads only missing required windows through the connected broker's ordinary
history service, writes and verifies them in Historify, then freezes the exact
calculation input. Changing stops, targets or sizing does not invalidate prices.
Longer holds may require additional dates. No separate engine price database is
introduced.

## Why repeated checks slowed down

The prior cache pass opened DuckDB separately for each symbol/window and published
an immutable recovery checkpoint after every read. Each checkpoint required fresh
storage admission scans. As saved evidence accumulated, the same directory tree
was inspected hundreds of times per run.

The cache pass now shares one short native connection for **at most 16 windows**,
then saves that group once. Actual OHLC rows still establish coverage; catalog
date ranges and counts cannot prove that there are no holes or malformed candles.
Queries keep their existing date, exchange, interval and per-window row limits.
The connection closes before progress publication or broker requests. Cancellation
is checked between queries and admissions. The time budget yields after an
admitted window; a local read group is bounded rather than forcibly interrupted.

Broker requests retain their pacing and individual write/readback checkpoints.
Interrupted checks retain admitted units; at worst an uncommitted group is read
again from the local archive. Exact saved replay continues to use frozen inputs.
No host schema, broker adapter, dependency or installer is changed.

## Measurements and limits

Read-only sampling of an existing archive, 143 daily symbols and 11,807 rows:
**2.424 seconds → 0.289 seconds**, 143 → 9 connections, identical returned rows.
This isolated database timing does not reproduce the complete reported 3–5 minutes.

A controlled 143-symbol / 20,592-candle cache fixture with 32,000 existing files
and actual recovery/artifact publication measured **53.58 seconds → 6.45 seconds**
under profiler overhead and development load. Checkpoints fell from 145 to 11;
storage scans fell from 289 to 29. This excludes live broker latency and the
worker's activity/lease transactions. It is evidence of reduced repeated work,
not a promised production duration. Worker logs now separately record the
Historify cache-pass duration without exposing credentials or price rows.

Focused acceptance covers exact evidence equality, daily/minute bounds, empty
archives, malformed responses, gaps, interrupted reads, resumed acquisition and
closed connections on success/error/cancellation. Repeated resource sampling
kept handles flat through 100 successes, 100 cancellations and 200 query errors;
RSS warmed and plateaued. Full installation qualification belongs to the next
versioned package; existing release ZIPs do not acquire source changes automatically.

## How the connected tools handle data

- [VectorBT](https://vectorbt.dev/api/portfolio/base/) simulates supplied price
  arrays and signals. Its data adapters can download data, but this integration
  supplies frozen Historify prices instead of selecting another provider.
- [Optuna](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/001_first.html)
  proposes parameters and evaluates our objective. It does not discover broker
  candle coverage. Every trial uses the prepared, frozen dataset.
- [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/data/) uses typed
  data and can query a Parquet catalog. Our connector translates the same frozen
  native inputs into its supported calculation runtime.

The transferable idea is to prepare data once and reuse it across calculations.
Moving data into another engine's catalog would not fix repeated orchestration
and would complicate the existing archive/evidence contract.

## Next delivery order

1. Install and verify this cache improvement against the current normal app,
   preserving configuration and saved runs. Publish the compatible source patch.
2. Reconcile newer local condition/recovery work with public onboarding and the
   OpenAlgo 2.0.2.6 host. Qualify a complete versioned package from committed source;
   retain public Setup/Start and Chartink guides. Do not package a live installation.
3. Finish recovery acceptance across paused/interrupted automatic studies, then
   test parameter-neighbourhood stability on frozen development periods before
   expanding search. Expose unsupported or unstable candidates honestly.
4. Complete portable reports and broader causal feature families within measured
   calculation limits. Treat rolling walk-forward as a separate protocol, keeping
   reserved final periods and exact saved evidence intact.

The source patch retains the public preview.6 host compatibility. Newer local
condition/recovery features and OpenAlgo 2.0.2.6 are separate unpublished work;
their installation is not evidence of public package compatibility.
