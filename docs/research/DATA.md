# Scanner Research data evidence

The primary product path is existing OpenAlgo Historify prices → download only
missing usable dates through the connected native broker → persist and read back
Historify → frozen research snapshot. Fyers is not required. Complete stored
coverage works without broker credentials. The database is the installation's
normal configured Historify archive; development alone requires isolated data.

New uploads use `openalgo-native-history-v1`: OpenAlgo's existing exchange calendar,
ordinary connected-broker history service and Historify storage. Broker timestamps
and OHLC are preserved, with no extra public corporate-action adjustment or mandatory
public-price comparison. Finite, positive, consistent OHLC and exact storage readback
are checked. The archive does not retain per-row broker lineage, so cached prices
are identified as OpenAlgo Historify with unknown original provider; no independent
identity or adjustment verification is claimed. Successful responses with absent
candles produce partial price coverage and pending affected trades. Request failures
remain resumable. The reference bundle below applies to explicit public imports and
older saved reference-qualified sources only.

Existing runs marked `NSE final CM bhavcopy` used the explicit public-file path,
not broker history. Their saved evidence must retain that identity. Broker
acquisition and exact native persistence have since been verified in the owner's
authorized Fyers minute and daily attempts. The corrected native daily backtest
now completes on 1,976 of 2,000 requested candles, with missing observations and
unresolved outcomes retained. Controlled adapter tests do not establish live
availability for other broker accounts.

The optional public-price path converts the reviewed public NSE final cash-market archives
into a frozen research snapshot. It does not require a broker login, load a
private database, or reclassify synthetic fixtures as exchange data.

## Configure and import

The reviewed source catalogs ship as metadata under `research/evidence_catalog/`.
They retain exact original manifest bytes and contain public URLs/hashes rather
than price archives. A fresh installation can provision its own baseline without
the previous research repository:

```bash
# The parent directory must already exist and be writable by the operator.
# The destination itself must be new; existing evidence is never overwritten.
uv run python tools/research_evidence.py install --download --destination /var/lib/openalgo-evidence
uv run python tools/research_evidence.py verify --directory /var/lib/openalgo-evidence
```

`--download` explicitly retrieves the pinned HTTPS URLs with bounded responses,
timeouts and no redirects. Missing, changed or corrupt files fail rather than
substituting a newer archive. Installation publishes the directory only after
every manifest, length and SHA-256 check passes. If any step fails, its temporary
staging files are removed and the destination stays absent. This command does
not obtain data-distribution rights; the chosen use still follows [LAUNCH.md](LAUNCH.md).

For an offline transfer, use `install --source /path/to/existing/bundle
--destination /var/lib/openalgo-evidence` instead. Only the 199 pinned source
files and two manifests are installed; other files from the source directory are
not copied. Verification covers the complete 201-file baseline, not just the
symbol/date subset consumed by a particular CSV. Set both the native web process
and research worker's `RESEARCH_PUBLIC_EVIDENCE_DIR` to the resulting directory.

Set `RESEARCH_PUBLIC_EVIDENCE_DIR` to a local directory containing the reviewed
`nse_daily/` and `exchange_evidence/` seed directories. The importer entry point
is `research.evidence_import.public_snapshot(signals, evidence_dir, progress)`.
The native research service owns selection of this configured directory; client
requests do not supply filesystem paths or a trust/verification flag.

Importer `nse-public-evidence-v2` accepts the exact reviewed manifest and verifies
SHA-256 for every consumed archive and action/restriction document. Its source
is the public evidence bundled at reference release 10.18, commit `ff86bf1`.
The manifest identifies 186 original final archives, 5 December 2025 through
4 September 2026, and 13 supporting documents. Changes to these pinned manifests
require an importer update with renewed evidence checks. Missing/corrupt files
fail explicitly. Nothing is copied into tracked Git data by import.

The other public price seed contains 1,006,650 rows across 1,415 symbols, with
provider metadata for only 298 symbols. Those provider-adjusted/legacy prices
are deliberately not used by this importer: packaging does not establish
native admissibility or complete instrument identity.

## Admission and quality

Every admitted official record has the manifest trading date, a valid ISIN,
one unambiguous normal series record (EQ, BE or BZ), positive trading volume
and transaction count, finite positive prices, and consistent OHLC geometry.
Alternate block, partly-paid or other series do not substitute for that record.
Ambiguous duplicates, malformed candles and unexplained ISIN changes are
quarantined. An unexplained new ISIN segment stays quarantined until an explicit effective-date mapping admits it. Repetition of the new ISIN or an unrelated action cannot establish continuity. Returning to the previously established ISIN can resume new entries; existing holdings already have an unresolved gap. Reviewed transitions retain a missing boundary bar so old-security holdings cannot silently cross identities. The two known ETF splits have explicit old/new ISIN mappings checked against clearing circulars and pinned ex-date archives. A greater-than-30% opening discontinuity from the previous observed
adjusted close is quarantined; it is a conservative anomaly screen, not proof
that all smaller corporate actions have been identified.

Missing observations are classified as head, tail and internal gaps. Explicit
zero-activity records remain `no_trade`. Absence from an archive is not by itself
proof of no trade: it remains missing, except within the specifically documented
SWANDEF/CLCIND restriction windows, where `restricted_no_trade` preserves the
supporting sources. Excluded/quarantined raw observations and reasons remain in
the quality evidence. No missing bar is fabricated and no provider's repeated
mark is promoted into a fill.

BE/BZ series and documented surveillance regimes can impose trade-to-trade
settlement, restricted trading frequency, or deposits. This cash simulator
does not model those requirements. The corresponding limitations appear with
the snapshot. It makes no universal tradability, liquidity or settlement claim.

OHLC begins at each symbol's first scanner signal. Seven earlier global calendar
sessions support scanner-count warmup without demanding unnecessary earlier
stock candles. The CSV does not establish scanner observations before its first
signal: zero counts in that earlier warmup are an explicit assumption, not
verified scanner history. Prices end at the actual archive tail; longer holding
horizons can remain pending. Coverage warnings remain visible even when a run
is allowed, and a source with no admitted requested symbols is blocked.

## Calendar, actions and causal cutoffs

The versioned 2025–2026 NSE calendar was rechecked against primary circulars on
6 September 2026: [2025 holidays](https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf),
[2025 budget session](https://nsearchives.nseindia.com/content/circulars/CMTR65729.pdf),
[2026 holidays](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf),
[15 January closure](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf),
and [1 February 2026 session](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf).
It includes the announced Muhurat sessions and fails for unsupported years.
The reviewed rules and source URLs are frozen in provenance. Calendar PDFs are
referenced evidence; their original binary files are not embedded in exports.

Original official prices are retained as `raw_bars`. The reviewed action
registry retains source document hashes, ex-dates, factors and formulas, including
the GROWWSLVR and IVZINGOLD unit splits and MAHAPEXLTD rights adjustment. No Yahoo
adjustment is mixed into this raw source. Rights are not treated as a free split;
subscription cash, entitlement sales and exercise decisions are not simulated.
The raw March 19 MAHAPEXLTD close is 126.90; its reviewed post-rights price basis
is 68.45. Independent regression checks preserve both values.

`research.data.cutoff_snapshot` truncates sessions, candles, identities, dated
receipts and findings and replays only actions effective by the cutoff. Future
action factors cannot change earlier whole-share sizing. Quarantined observations
stay excluded; raw records retained for audit cannot re-enter through a cutoff.
Saved snapshots include normalized price evidence, source URLs/hashes, action
rules, identity observations and quality findings. They do **not** embed entire
original ZIP/PDF binaries; retain the configured source bundle when binary
source replay is required.

## Automatic daily and minute requirements

`research.requirements.select_interval` and `build_plan` share planner version
`research-price-requirements-v2`. Date-only scanner signals select daily EOD
prices, including ordinary stop/target/trailing settings under the recorded daily
bar policy. Timestamps, explicit intraday horizon, minute holding or entry/exit
clocks select one-minute prices. Risk percentages alone never force minutes.

Daily plans retain `required_dates` per symbol: the union from each next-session
entry through its holding deadline across every requested candidate. Scoped daily
acquisition reads and downloads those dates only, skipping unheld gaps and already
usable stored prices. It stores broker `D` candles in the same Historify database
and verifies their readback. Scope belongs to checkpoint identity, coverage and
causal cutoffs; a wider holding/search plan triggers automatic preparation of
its missing dates. Legacy unscoped daily artifacts retain their existing meaning.

The bounded minute plan retains the scheduled exchange-minute grid and per-symbol
union of required holding windows across the requested experiment. Normal NSE
sessions contain 375 minute-open slots; reviewed special-session hours override
the normal schedule. Unknown special-session hours fail explicitly.

`services.research_intraday.acquire_intraday` reads native Historify `1m` rows
first. Complete usable coverage never resolves broker credentials. Missing rows
are requested through the connected native adapter in at most five-calendar-day
windows, saved to Historify and reread exactly before publication. Native history
accepts date windows, so a response can contain already cached minutes; those
observations do not replace frozen cache values. Daily rows cannot fill minute
gaps. Checkpoints retain admitted rows and hashed receipts for cancellation,
expiry and retry. Each worker pass makes at most 500 broker calls; local cache
reads do not consume that allowance. Larger plans continue automatically from
the same job's checkpoint after a fully successful pass. Frozen rows are not
read or downloaded again. Incomplete or failed responses stop automatic
continuation so unavailable data cannot cause an endless retry loop. Limits
also include 10,000 rows per read/response, the shared aggregate bar cap,
128 MiB receipts and a 1,800-second between-call budget per pass.

Preparation progress measures processed work across stored-price reads, broker
requests and final checks. Price coverage is retained separately: a processed
request can still have missing or rejected candles. Progress is monotonic
through a pass and resume; only successful publication reaches 100 percent.
Checkpoint updates use incremental symbol/receipt manifests, not full repeated
serialization of the accumulated minute download.

Minute qualification checks timestamp alignment, duplicates, finite OHLC geometry,
reviewed daily instrument identity and raw basis, and consistency with daily
high/low bounds. It does not equate minute and daily OHLC, or independently verify
an exchange minute tape. Sparse or malformed required slots remain unresolved.
The frozen snapshot records `interval=1m`, `temporal_version=minute-open-v1`,
`required_timestamps`, the complete scheduled timeline and this limitation.

## Native Historify-first acquisition

`services.research_acquisition.acquire_history` first reads each symbol's stored
daily rows through the native Historify connection convention. It checks those
observations and records immutable archive receipts. Only gaps in usable coverage
resolve the owner's current native broker session and call the shared OpenAlgo
history/symbol services. The `stored_only` mode never resolves credentials or
downloads. The default `stored_then_broker` mode uses the currently connected
broker, preserves the actual returned prices, writes them with native upsert, and
requires an exact read-back before admitting them to the frozen snapshot.

Legacy Historify rows have no broker lineage column. Their original provider is
recorded as unknown; they are never relabelled as the current broker. New download
receipts identify the actual adapter. A later broker change can fill remaining
holes without changing already saved observations or claiming one provider for
all rows. Public reference prices are never used to fill a missing native candle.

Acquisition uses the shared native history pacing and
For the retained daily path it bounds work to 500 daily requests, 300 calendar days per request, 25,000 signals
and 1,800 seconds between-request elapsed time. It never downloads per optimizer
row. Authentication lookup, history requests and archive writes occur only on
invocation. Credentials never enter persisted receipts.

Each response produces a hashed, atomically published receipt containing request
window, provider/mapping, timestamp timezone, source response hash, normalized
observations, admitted dates and result/error classification. Expiry stops later
requests; partial success never implies complete coverage. Cancellation and
budgets are checked between requests and before ingestion. Where available, the
native adapter's richer evidence method is preferred. Fyers retains its bounded
transport, response hashes and retry/expiry outcomes. Other brokers use their
existing `BrokerData.get_history` implementation and record
`evidence_level=native_adapter`, `raw_transport_captured=false`. This captures the
normalized table returned by OpenAlgo, not hidden transport attempts or discarded
errors. Empty tables are unavailable data, never proof that no trading occurred.
Unsupported intervals/history fail explicitly. Ordinary API behavior is unchanged.
Generic cancellation/deadline checks run before and after the adapter call; they
cannot interrupt its internal requests or retries. The external worker maintains
its lease while an adapter blocks, keeping this work outside the Flask request.

Bare broker candles remain unverified. Admission compares each broker
OHLC to the checked official raw record (relative tolerance 0.00002 or absolute
INR 0.02), requires recorded official ISIN/series identity, and applies the same
reviewed action overlay to the actual broker prices. Unmatched newer observations
are preserved and quarantined until matching reviewed evidence is supplied.
This does not certify previously unseen broker adjustment behavior or new years.

Acquisition sidecars are explicit persistence; a successful history response alone
does not establish stored-price readiness. The production feature supplies both
the native reader and writer; direct adapter tests may inject these boundaries.
`native_historify_ingest` uses native schema/upsert functions and the configured
`HISTORIFY_DATABASE_PATH` (or native default). It rejects a module previously
initialized with another path; `HISTORIFY_DATABASE_URL` is not the effective
setting. Configure paths before starting web/worker processes. Do not point
research development at a live installation's archive.

No live broker login/download has been exercised. Controlled tests cover the
Fyers rich transport and generic native history paths, including Zerodha and Dhan
through the HTTP worker workflow and real temporary DuckDB persistence. The native
code inventory contains 36 adapters; 31 declare daily resolution, but that is not
a promise of historical NSE daily coverage. Some adapters have no history,
Tradejini declares intraday only, Motilal's daily path is today-only, and crypto
or sandbox adapters do not imply real NSE equity coverage. Capability, venue,
date-range, account entitlement and actual response checks remain necessary.

Live acceptance must record which brokers and ranges actually passed download,
stored reuse, expiry/recovery and exact saved reopening. A complete stored archive
is usable without requiring another broker login. Production deployment is separate.

## Local evidence and resource audit

On Windows Python 3.12, the data tests cover actual reviewed archives/actions,
calendar specials, malformed/ambiguous/no-trade/series admission, coverage
classification, causal future-action mutation, native adapter partial/expiry/
rate-limit responses, cancellation, timestamp conversion, duplicate responses,
and actual isolated native DuckDB ingestion/readback. The external bundle test
runs when `RESEARCH_PUBLIC_EVIDENCE_DIR` is explicitly configured.

A generated benchmark input of 18,310 unique dated signals across the 298 public
metadata symbols imported 54,691 admitted official bars over 193 sessions in
2.50 seconds on this workstation; sampled peak process RSS was 102.9 MB and
compact snapshot JSON was 17.0 MB. Scanner membership was generated for this
measurement; it is not a recovered user upload or historical scanner claim.
This is import performance, not an optimizer timing. Structural caps are 3,000
sessions and 2,000,000 admitted bars; those upper limits are not measured full
capacity guarantees.

The applicable `fd-audit` was applied to file/ZIP streams, temporary publication,
native DuckDB ownership and retained collections. Context managers/finally
release streams and temporary files on success, exceptions and cancellation.
The importer has no global parsed-frame cache and keeps only requested symbols;
archive entries are capped at 20 MB decoded/5 MB compressed. Repeated successful
and rejected archive parsing (150 iterations) stayed within a three-handle
allowance. Native DuckDB connections use their context-managed native owner.
The adapter owns no HTTP pool or executor and reuses native history services.


## Versioned official extensions and retry evidence (audit A1/A6/A7)

`extend_official_bundle(evidence_dir, extension_dir, end_date, ...)` is the
supported server-side update operation. The caller supplies configured local
directories and a reviewed exchange-session end date; clients cannot submit an
arbitrary source URL or declare data verified. The worker acquires original
archives from the fixed official NSE HTTPS endpoint without following redirects.
The last two baseline sessions must match the pinned archive hashes exactly.
All later sessions must have complete correctly dated archive files. Publication
uses content-addressed files and an atomic current-manifest pointer; failed or
cancelled updates leave previously published manifests and snapshots intact.

`public_snapshot(..., extension_dir=...)` verifies this lineage and consumes the
extension. New-only CSVs check their identity against the baseline overlap;
new ISIN segments cannot certify themselves through repeated later observations.
The extension applies only existing reviewed action/identity rules. New
corporate-action factors and identity transitions require a reviewed registry
update with effective dates, exact old/new mappings and source evidence; this
API cannot accept replacement factors or verification flags. Unsupported calendar
years remain blocked until their calendar is reviewed. Raw prices and explicit
unknown-action/cashflow limitations remain visible. This extends supported price
dates without modifying the original pinned bundle or old saved evidence.

`official_bundle_status` exposes manifest date availability and supported calendar
years before preparation. It is a lightweight identity check, not a claim that
all requested symbols have verified complete coverage.

Acquisition `on_checkpoint(state)` records successful windows and accumulated
observations after each chunk. `prior=state` verifies the immutable request identity
and requests only unsuccessful windows. Definitive empty success is retained as
empty evidence rather than repeatedly downloaded. Failed/expired attempts stay in
the receipt history; new authentication is never part of persisted state. The
returned snapshot includes `acquisition_checkpoint` for the job owner to retain
separately. `acquisition_receipts(snapshot, archive_dir)` verifies sidecar hashes
and returns exact receipts for embedding in the immutable saved source.

Regression tests drive actual native history and Fyers transport with fabricated
raw HTTP responses. They prove one-call expiry, three-attempt exhausted 429/503,
legitimate empty responses, raw duplicate quarantine, missing-only retries,
cancellation/deadlines and preservation of the ordinary history contract. No
live token, broker request or private archive was used. Generated official ZIP
fixtures test extension, overlap conflict, persistent identity segments, reviewed
mapping boundaries, source preservation and unknown calendar years.
