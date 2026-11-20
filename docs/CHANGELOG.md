## [Research 0.1.0-preview.36] - 2026-11-12

Measured development feedback, conservative checked-branch comparison and
verified actual setting changes in the Research journey. Validation and account
budgets are unchanged. [Notes](research/releases/0.1.0-preview.36.md).

### Contributors

- **@mamamiya7** � Research decision feedback, checked-branch selection, UI and qualification.

## [Research 0.1.0-preview.35] - 2026-11-12

OpenAlgo 2.0.3.3 plus reviewed November fixes, charts 2.7.1, strict Fyers
history integration and required installer migrations. Research settings,
validation and saved evidence contracts are unchanged. Local update; public
download remains preview.34. [Full notes](research/releases/0.1.0-preview.35.md).

### Contributors

- **@marketcalls** — OpenAlgo maintenance, security, alerts, charts and release.
- **@Kalaiviswa** — broker data/order fixes, including Fyers history (#2438).
- **@Mystery01092000 (Pranav Tomar)** — cached instruments (#2406) and Paytm
  stop orders (#2404).
- **@raghav-iit (Raghav Verma)** — structured Paytm cancel errors (#2374).
- **@mamamiya7** — Research integration, strict history validation, installer
  migration coverage and saved-result compatibility qualification.


# Changelog

## [Research 0.1.0-preview.34] - 2026-11-04

OpenAlgo2.0.3.2 integration, secure broker setup, reviewed automatic research and
exact saved-result reopening. Full search allowance and validation are retained.
See [release notes and qualification](research/releases/0.1.0-preview.34.md).

### Contributors

@mamamiya7, @marketcalls, @Kalaiviswa, @dmchandru,
@SatriaBagusNurhadianto and @dvygo. Original upstream credits remain below.

All notable changes to OpenAlgo will be documented in this file.

Each release adds a stanza here summarising what changed and who contributed.
The full notes for a release, with commit SHAs and the reasoning behind each
fix, live in [docs/releases](releases/).

## [2.0.3.4] - 2026-11-17

### Alert Channels and Nuvama Release

32 commits since 2.0.3.3, excluding automated frontend build commits and
including this release. Full notes:
[version-2.0.3.4-released.md](releases/version-2.0.3.4-released.md).
This release adds two migrations, `migrate_alert_channels.py` and
`migrate_password_reset_grants.py`, both required. Run `migrate_all.py` and
restart.

### Highlights

- One Alerts page for Telegram, WhatsApp, Slack and Discord, with Slack and
  Discord from the API, Flow and MCP (#2435, #2440).
- The Nuvama broker (#2461).
- Four security fixes: the live Socket.IO channel, Telegram `/link`, broker
  postbacks and password resets.
- Scheduled Flow workflows keep running when the database is busy (#2355,
  #2326).
- The Latency Monitor reports orders apart from data calls (#2421, #2286).
- Elliott wave counts and openalgo-charts 2.7.3 on every chart.
- Fixes for nine brokers, among them Paytm Money stop orders (#2404) and
  market price protection for cash symbols ending in CE or PE (#2388).

### Security

- **The live Socket.IO channel needs a signed-in session.** A client with no
  login could connect and receive live order events, analyzer updates, the
  signed-in device list, scalping levels and the WhatsApp pairing QR code. A
  Socket.IO client of your own now needs a signed-in session.
- **Telegram `/link` checks this instance's API key.** Anyone who found the
  bot could link a chat by naming a server of their own, then switch between
  Live and analyzer mode or stop every Python strategy. A chat linked before
  the API key was last regenerated is asked to send `/link` again.
- **Broker postbacks need proof of their sender.** Anybody who could reach
  the server could post a fake fill for Dhan, Fyers, Upstox, Angel One or
  AliceBlue. Postbacks from those five are refused; their order updates come
  through the order feed OpenAlgo opens at login. A postback URL registered
  at those brokers no longer delivers anything. Zerodha's signed postbacks
  are accepted as before.
- **A password reset works once and lapses** after 15 minutes (TOTP) or one
  hour (emailed link). A copy of the reset cookie could set the password a
  second time. A reset started before the upgrade has to be started again.
