# Volume provenance and constrained portfolio research — 21 October 2026

## Scope and compatibility

Automatic recipe `automatic-hypotheses-v13` builds on v12. It retains the 108
registered stock/market choices, 272 proposals, four independent fits and at most
384 native accounts. Execution policy v4, risk limits v3 and native VectorBT
adapter/policy v5 add historical volume participation and correlation-aware entry
allocation. New unconfigured automatic studies select v13; saved older recipes,
policies and numerical histories keep their identities.

This is a bounded execution/allocation increment, not covariance optimisation,
live rebalancing or proof of executable liquidity. The previous strict
`research-volume-admission-v1` contract is unchanged: these native histories do
not establish point-in-time corporate-action parity. The implemented cap uses a
separately named, explicit historical-coordinate simulation model.

## Native volume evidence and reuse

Research uses the ordinary OpenAlgo history service and matching Historify
archive. There is no second broker downloader or generic database migration.
Before normalisation, the acquisition layer retains whether volume was actually
supplied. After archive persistence and exact readback, a research-owned sidecar
binds the observed OHLCV/timestamp, symbol, interval, provider, reviewed adapter
hash, quantity-unit profile and capture time. Missing values normalised to zero
never become observed zeroes.

The first reviewed producer profile is **Fyers daily NSE cash equities**. Its
adapter fingerprint is normalised across Windows/Linux line endings. Other
brokers retain ordinary OpenAlgo price support; their volume cannot qualify this
model until their producer semantics are reviewed. An unavailable-volume outcome
offers **Use broker-volume participation limit** off, preserving correlation
controls and the remaining automatic programme. The system never silently drops
a requested limit.

An opted-in run treats missing provenance as missing required data, even if OHLC
is already cached. The existing coalesced history path upgrades those windows
once. Matching sidecars are reused across later runs and restarts. A changed
Historify candle invalidates the corresponding evidence. Unknown-unit or absent
volume observations remain explicitly unavailable and do not cause endless
redownloads. The frozen feature service also matches volume-source OHLC to the
retained warmup context before accepting it.

Sidecars are bounded to 3,000 dates and 2 MiB per instrument, 2,048 files and
64 MiB overall. Writes are atomic; no credentials are stored. Capture time is an
audit time, not proof that retrospectively adjusted history existed at the entry.

Primary source references: Fyers [historical OHLCV contract](https://fyers.in/api-docs/ai/skills/market-data/),
[stock-volume units](https://support.fyers.in/portal/en/kb/articles/how-is-the-volume-for-indices-calculated-in-fyers)
and its [support response on adjusted price and volume](https://fyers.in/community/t/are-stocks-price-and-volume-adjusted-for-corp-actions-like-splits-bonuses-dividends-etc-in-daily-and-1-min-timeframe-sourced-via-historical-api/22387).
These support the declared producer model; they do not establish executable
spread, depth, market impact or historical point-in-time adjustment lineage.

## Shared daily participation budget

At each entry, compute the mean of exactly 20 prior expected completed daily
volume observations. The entry session and future observations are excluded;
gaps, missing fields, unreviewed units and nonpositive means are unavailable.
The default fixed participation limit is 1%, editable above zero through 10%.
It is a constraint, never an optimisation axis.

All strategies and repeated signals share one symbol/session share budget.
Only confirmed native whole-share buys consume it. Rejections reserve nothing;
sales never replenish it. The next session resets the budget. Source identities
and the mean cannot change after the first fill in that symbol/session. Cash,
fees, slippage, gross/symbol exposure, position count, stop risk and turnover
constraints still apply. Exits remain unrestricted by entry budgets.

Missing inputs in any admitted clock branch exclude the same original
opportunity across branches, rather than allowing a favourable missing-data cohort.
If no requested capacity window qualifies, preparation stops before optimisation
with an actionable explanation, not a claim of no financial edge.

## Correlation-aware entry allocation

Exactly 60 daily returns require 61 consecutive expected completed closes before
the entry. Use aligned observed dates and completed-close availability; no
interpolation, entry-day close or forward window is permitted. Constant series,
gaps and nonfinite returns produce unknown relationships. Pearson calculation
uses the pinned NumPy runtime; see its [correlation definition](https://numpy.org/doc/stable/reference/generated/numpy.corrcoef.html).

For each entry, count existing same-symbol exposure and directly related held
symbols whose positive correlation is at least 0.8. The fixed correlated-exposure
ceiling defaults to 25% of marked account equity and is editable through 100%.
The new order is clipped while accounting for entry friction and every existing
hard cap. There is no transitive grouping or negative-correlation hedge credit.
If a held pair is unknown, the entry is explicitly skipped; it is never treated
as uncorrelated. Existing holdings are not rebalanced when correlations change.

Feature preparation is bounded to 25,000 entry windows, 500,000 relationship
values and 64 MiB. Features bind their original opportunity/clock mask and frozen
context; native validation checks the exact symbol, date, source and availability.
Trials attach saved observations without acquisition or feature recomputation.

## Recommendation and recovery checks

V13 retains v12's later-window, higher-cost, paired daily-range and actual
neighbour checks. It additionally verifies the native v5 order/ledger facts and
constraint receipts. Actual buys reconcile to symbol/session participation sums;
per-entry capacity and correlation feature identities and recorded cap arithmetic
must match the frozen assumptions. Correlation arithmetic uses the engine's
recorded marked exposure/equity; this audit does not independently reconstruct a
second portfolio engine from prices.

Every development reference, chosen account and higher-cost comparison must have
supported constraint evidence before a non-reference recommendation is frozen.
Explicit unavailable-data skips make it incomplete. Invalid native accounting or
source evidence stops the run. Only compact sealed audits are added to saved
candidate metadata; no per-trial ledger duplication is introduced. Final-period
outcomes cannot rank candidates or supply development recommendation evidence.

The reference means original trade settings **under the same newly declared
constraints and common input mask**. It does not assert numerical equality with
an older v12 account. Completed recovery rechecks the selected report's audit
against its saved account evidence. Chosen-setup replay retains both modes,
limits, feature version and exact frozen source; new CSV addition preserves the
account-wide policies.

## Qualification and activation

884 distinct scoped backend checks and 263 frontend checks pass. These include
actual native v5 fill accounting, causal feature production, constraint audits,
ordinary acquisition/cache reuse, legacy golden report identities and the complete
272-proposal VectorBT/Optuna service with pause/recovery. A separate positive
development controller test verifies all 16 audits before final evaluation,
deliberately worse final outcomes cannot rerank selection, exact completed recovery
and rejection of rehashed saved-audit corruption. Its synthetic economics and audit
stub qualify orchestration; separate genuine native tests qualify fill arithmetic.

Exact selected/trial replay preserves the native ledger, summary and limits with
archive, history, broker credentials and Optuna forbidden. Real setup create,
read, named version save, read and reuse retain the versioned modes and limits.
The discovered draft risk-version admission and audit-before-enrichment defects
were fixed and covered before activation.

100 repeated native accounts have unchanged Windows handle counts and a retained
RSS increase of 155,648 bytes with flat later checkpoints. Cache capture/read,
changed-source and malformed-read repetitions retain stable handles and bounded
memory; atomic write failures remove temporary files. Source compatibility,
TypeScript, lint and the production build pass. No installation dependency or
generic database schema changed; this is a populated local update, not a claim
of a new full Windows/Linux release qualification.

Controlled broker-labelled fixtures are not represented as real broker downloads.
Local activation/source push are recorded in STATUS after verification. Public
ZIP publication remains separate from source push and local installation.

## Remaining scope

Broader producer profiles, independently attested corporate-action quantity
bases, spread/depth/impact models, covariance/factor/sector allocation, continuous
rebalancing and wider search-seed/false-discovery calibration remain separate
work. The existing 108 indicator choices do not become a broader method catalogue
merely because portfolio constraints were added.
