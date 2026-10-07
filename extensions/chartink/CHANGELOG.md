# Extension releases

## 0.1.2 — source release; Chrome Web Store submission pending

- Three clear popup steps: connect the running OpenAlgo address, choose Chartink history, then review saved research before running it.
- Clickable Chartink scanner link when the current page cannot be imported, plus a direct setup guide.
- Clear connection, reconnect and pending-import actions; capture stays unavailable until the selected address is connected.
- Popup interaction tests cover pairing, import recovery and actionable guidance.

Capture, permissions, connector protocol 1 and native research calculations are unchanged. Requires OpenAlgo Research **0.1.0-preview.5 or newer**.

## 0.1.1 — prepared, not yet published

- Original green connector logo in Chrome's toolbar, extension listing and popup.
- Offline Privacy and online Help links in the popup.
- Explicit data retention, destination and deletion disclosures.
- Optional plain-HTTP permissions limited to localhost and loopback; remote hosts require HTTPS.
- Reproducible store ZIP with `manifest.json` at the root, icons, license and notices.
- Public publishing guide, store copy and original promotional artwork.

Capture and native research calculations are unchanged by this release. A matching
OpenAlgo Research app with Chartink connector protocol 1 is required. The older
Research `0.1.0-preview.4` app release alone is insufficient.

## 0.1.0 — initial development connector

- User-triggered capture of Chartink's existing historical CSV export.
- Scoped pairing with the user's selected OpenAlgo installation.
- Saved experiment import, interruption recovery and duplicate-safe handoff.
