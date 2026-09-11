# OpenAlgo Research — Chartink: privacy policy

Effective: 11 September 2026 · Extension version: 0.1.1

This independent, free, open-source browser extension sends a Chartink scanner's historical signal export to the OpenAlgo Research installation you choose. The publisher is the OpenAlgo Research community project maintained by [@mamamiya7](https://github.com/mamamiya7). It is not an official Chartink or OpenAlgo extension.

## What the extension handles

When you choose **Research in OpenAlgo**, the extension captures the selected scanner's existing historical CSV export, scanner URL and title, selected-period label, capture time, and any visible repaint notice. This can include names or other content you put in that scanner. It does not enumerate your browsing history or capture unrelated pages. It processes the chosen scanner URL as part of the import.

The extension also handles the OpenAlgo address you enter, connection status, and the title and link of the last saved experiment. It does not read or store your passwords, broker keys, cookie values, account balances or payment details. Your browser and OpenAlgo handle your existing OpenAlgo login session.

## Where information goes

The selected export and its source information go to **your chosen OpenAlgo installation** to create a saved research experiment. The extension checks the destination origin and tab before delivering the import. Remote OpenAlgo installations require HTTPS; HTTP is supported only for loopback addresses on your own computer.

The extension has no publisher-operated data collection server, analytics, advertising or Chrome sync storage. The publisher does not receive your imports through the extension, sell them, or use them for advertising. It does not download or execute remotely hosted extension code. Chartink and your OpenAlgo operator have their own services, logs, storage and applicable policies. If you choose a remote OpenAlgo server, its operator receives the import.

## Storage and deletion

- One pending import, with at most 8 MiB of CSV, is held in Chrome session storage while a handoff is incomplete. It is removed after successful acknowledgment, when you choose **Discard pending import**, or when the browser session ends. An import becomes unavailable after one hour; expired bytes are removed on a subsequent cleanup operation, rather than by a deletion timer at exactly one hour.
- Your OpenAlgo address/status and the most recent experiment title/link remain in this extension's local Chrome storage until replaced, cleared or the extension is removed. They are not synced to your Google account by the extension.
- Removing the extension through Chrome's extension settings removes its local extension storage. It **does not delete imports already saved in OpenAlgo**. Archiving in the Research library hides work without erasing its original export. The library restricts deletion of experiments with saved runs, versions or related experiments. Actual removal of saved evidence, logs and backups depends on the installation's retention and administrator controls; contact that installation's operator for deletion requests.

The extension uses Chrome's storage facilities; it does not add its own encryption of stored archives.

## Access permissions

Chartink access supports the scanner-page button and the export you request. `activeTab` supports a toolbar-initiated capture when persistent site access is restricted. `scripting` runs the extension's bundled capture and import bridge; `storage` keeps connection and recovery state. Access to an OpenAlgo host is optional and requested when you connect to that address. You can review or revoke site access in Chrome's extension settings.

Importing does not place orders, start broker price downloads, or launch backtests automatically. Those actions belong to your separate OpenAlgo application and its controls.

## Contact and changes

For questions or a data-handling concern, contact the maintainer through the project's [support issues](https://github.com/mamamiya7/openalgo-research/issues). Do not post passwords, broker credentials, private exports or personal trading information in a public issue. The maintainer cannot delete data on an OpenAlgo server they do not operate; contact that server's operator.

Changes to extension data handling will be reflected in this policy and the extension's release notes. The extension's use of information received through Chrome APIs follows the [Chrome Web Store User Data Policy](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq), including its Limited Use requirements.
