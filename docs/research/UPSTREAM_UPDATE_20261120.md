# OpenAlgo 2.0.3.4 host update — 20 November 2026

Integrated the latest published OpenAlgo release, tag
`openalgo-alert-channels-nuvama`, commit
`9c850e5c745ad8e7f9f8b779c6e4ccc327e18c5a`. This advances the reviewed
`be6c3bbe4887a615d66eeafe9dbf3b1294f29bc0` snapshot to platform **2.0.3.4**.
Unreleased upstream main changes are excluded. Host qualification initially used
preview.38 with its local decision-loop fixes; the combined public package is
[preview.39](releases/0.1.0-preview.39.md).

## Included changes

- Charts 2.7.3, including corrected chart studies and Elliott wave drawings.
- Flow and Historify scheduling resilience when SQLite is busy.
- Latency reporting grouped by endpoint, separating orders from market data.
- Nuvama broker implementation and updated broker metadata. Existing private
  broker enablement and credentials are preserved; adding a broker remains a
  user configuration action.
- dnspython 2.9.2, tornado 6.6.1 and soupsieve 4.1.

Official alert-channel, password-reset, postback and Socket.IO protections
were already present in our prior reviewed snapshot and remain included.
Required alert-channel and password-reset tables already exist in the installed
app; the update does not rewrite saved research or market data.

## Qualification and limits

261 focused backend checks and 196 frontend checks pass, including scheduler,
latency, MCP integrity, installation, research diagnoses, repetition screening,
native chart integration and saved trade charts. One platform-specific backend
case is skipped. The combined TypeScript/production build and frozen Python
lock compatibility pass. A separately attempted broad position-read test file
cannot collect because its pre-existing broker specification lacks `sahi`;
this is retained as an unverified area, not counted as passing.

The existing five-stock saved qualification is reopened with provider calls and
calculations forbidden. No new scanner, search or strategy experiment is needed
for this host update. Validation thresholds, search allowances, connector
versions and financial settings are unchanged.

Static resource review covered scoped database connections, bounded latency
cache, scheduler recovery state and Nuvama HTTP/socket teardown. No confirmed
new persistent leak was found. Nuvama stop does not join a pending connection,
which can take its 15-second connection timeout to finish. Live broker operation
and long-running descriptor/RSS behavior have not been qualified.

Upstream implementation and release credit remains with the contributors in
[the official release notes](../releases/version-2.0.3.4-released.md).
Ignored `.agent-native/upstream-update/` retains the exact installation and
verification receipt until a subsequent host upgrade supersedes it. Disposable
merge stages and source preimages are removed after verified installation,
or retained pending the reviewed manual cleanup when automatic deletion is blocked;
existing private settings, saved data and historical qualification are retained.
