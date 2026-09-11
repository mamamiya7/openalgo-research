# Publish the free Chartink connector

Prepared 11 September 2026 for extension **0.1.1**. This is a release plan, not a submitted or approved store listing.

Publish it as **one community Chrome extension**, with its source in this repository and an install button on the Chrome Web Store. It connects to the user's own compatible OpenAlgo Research app; it is not a hosted backtesting service. No new website, analytics service or payment system is needed for this release.

## What is ready, and what remains

| Item | State |
| --- | --- |
| Original green connector logo, toolbar icons and 440 × 280 promotional tile | Prepared in [store artwork](extension-store/README.md) |
| Version 0.1.1 ZIP with manifest at its root, license and original artwork | Build with `python extensions/chartink/package.py`; the output and checksum are under ignored `.agent-native/chartink-package/` |
| Public policy, bundled offline policy, support links and store copy | Prepared: [privacy](CHARTINK_PRIVACY.md), [listing and review instructions](CHARTINK_STORE_LISTING.md) |
| Public app release supporting Chartink connector protocol 1 | **Required next.** Installed development source supports it; published `0.1.0-preview.4` alone does not. Do not direct new users to an incompatible release. |
| Real store screenshots and fresh-profile acceptance | **Still needed** against the final compatible release. The logo/promo tile are not screenshots. |
| Chartink export/retention terms | **Clarify before broad promotion**, as described below. |
| Publisher identity, verified contact email and review submission | Owner actions in Google's dashboard; no account registration or submission has been performed. |

## Step by step

1. **Test the new build.** Reload the unpacked extension at `chrome://extensions`; if installing from the ZIP, extract it first and select the folder containing `manifest.json`. The toolbar and popup should show the green connector logo. Follow the acceptance checks below. No OpenAlgo restart is needed just to update the extension.

2. **Release the compatible app and source together.** Package and test the Research application containing connector protocol 1, then publish its versioned release. Tag the exact extension source (suggested tag: `chartink-extension-v0.1.1`), attach the ZIP and `SHA256SUMS`, and add the tested app release/version to the extension README and reviewer instructions. These publishing actions are a subsequent step, not part of this preparation.

3. **Complete the narrow legal/content checks below.** Keep the repository's existing AGPL v3 license and notices. Resolve the specific Chartink export question. Use the original logo and independent-community wording. No separate software license was invented for the extension.

4. **Register your publisher account.** Use a Google account you intend to maintain, protect it with two-step verification, and open the [Chrome Web Store developer dashboard](https://chrome.google.com/webstore/devconsole). Pay the one-time registration fee shown there; the extension can remain free for users. Choose your real publisher name and verify a contact email you monitor. Complete any identity, location or trader-status questions using your actual circumstances; “free” alone is not an answer to all such questions. [Registration guidance](https://developer.chrome.com/docs/webstore/register), [account setup](https://developer.chrome.com/docs/webstore/set-up-account).

5. **Verify the public help and policy links.** The source update includes these documents in the repository. Confirm the links in [store copy](CHARTINK_STORE_LISTING.md) work without signing in before submission. GitHub can host the policy and support instructions. Enable a private security-reporting route in the repository and identify it in support documentation before inviting vulnerability reports.

6. **Create the store item and upload the ZIP.** Choose **New item** in the developer dashboard and upload `openalgo-chartink-0.1.1.zip` directly. Its `manifest.json` is now at the ZIP root, as Chrome requires. Use the prepared name, summary and description; choose the closest available productivity/research category and accurate supported regions/language. [Package preparation](https://developer.chrome.com/docs/webstore/prepare).

7. **Add the images.** Upload `icons/icon128.png`, the 440 × 280 promo tile, and at least one real product screenshot at 1280 × 800 or 640 × 400. Three screenshots are useful: connect/import; the saved scanner with dates/symbols; and research setup in OpenAlgo. Clearly label app screenshots as the separate required OpenAlgo Research app. Use a scanner you can publicly show and remove personal tabs, usernames, holdings and notifications. [Image requirements](https://developer.chrome.com/docs/webstore/images).

8. **Fill Privacy practices and review instructions.** Use the prepared single-purpose statement, permission explanations and data mapping. Declare the chosen scanner URL/title and exported website content even though the publisher has no collection server. Add the public policy URL. Describe the self-hosted app prerequisite and give reviewers a reproducible test; if credentials are necessary, use a dedicated test account, never your trading account. [Privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy), [local-data disclosure](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq), [review instructions](https://developer.chrome.com/docs/webstore/cws-dashboard-test-instructions).

9. **Submit to review for a small tester release first.** Choose **Private → trusted testers** and add testers in the dashboard. This is still subject to store review. Check permissions, account login and capture on their machines. After acceptance and feedback, change distribution to **Public** and complete any review Google requests. You can defer automatic publication while reviewing the release; Google documents a 30-day publication window after approval for a staged submission. Review times vary. [Distribution options](https://developer.chrome.com/docs/webstore/cws-dashboard-distribution), [submission process](https://developer.chrome.com/docs/webstore/publish).

10. **Share one install link.** Put the Chrome Web Store install link next to the compatible app download and GitHub source. Introduce it to the OpenAlgo community as a community connector; offer a focused discussion or integration contribution. An upstream merge is separate from publishing your own licensed community extension. Keep the same store item for updates, increase its version, preserve tagged source and release notes, and retest when Chrome, Chartink or the import protocol changes.

## Legal and policy decisions

| Area | Practical action |
| --- | --- |
| Software license | The repository uses GNU AGPL v3. The ZIP includes the identical license and attribution. Publish the corresponding tagged source, including build scripts and changes; preserve notices. The app and third-party libraries retain their own applicable notices. See [repository license](../../License.md). |
| Brand names | Use the original logo and clearly say “independent community connector.” Chartink/OpenAlgo names describe compatibility; do not imply endorsement or copy their logos. [Chrome impersonation/IP policy](https://developer.chrome.com/docs/webstore/program-policies/impersonation-and-intellectual-property). |
| Chartink export rights | Chartink's terms discuss temporary personal, noncommercial downloads and separately refer to a GNU license for user-created scans. They do not clearly settle persistent saved CSV evidence or imports into a remote self-hosted app. Request clarification for this exact use before broad promotion; this ambiguity is not a finding that the connector is unlawful. Do not redistribute scanner exports in the extension or promotional assets without the necessary rights. [Chartink terms](https://chartink.com/articles/terms-of-usage/). |
| Privacy | A free extension that handles data still needs truthful disclosures. This release sends selected content to the user's chosen server, uses Chrome storage, and has no publisher analytics/collection backend. Publish and maintain the prepared policy; reassess before adding telemetry, accounts or hosted storage. [Chrome privacy policy](https://developer.chrome.com/docs/webstore/program-policies/privacy). |
| Publisher declarations | Use your actual identity/contact and answer Google's current legal-status questions accurately. Get specific advice if a declaration is unclear for your location/business; do not create a company or claim an exemption merely because this is open source. |
| Trading claims | Describe signal import and research, not investment advice, automatic trading or guaranteed returns. Broker history and source access depend on separate services and their terms. A large financial disclaimer is unnecessary inside every extension interaction. |

Suggested message for Chartink's official support channel — draft only, not sent:

> I maintain a free, open-source community connector. A user clicks it on a Chartink scanner page; it invokes the existing historical Download → CSV control available to that user and imports that export into the user's own OpenAlgo Research installation. That installation retains the original CSV with private backtest results, either locally or on the user's own HTTPS server. The extension does not bypass access controls, bulk-crawl scanners, or distribute a shared dataset. Could you confirm whether this export, retention and self-hosted use is permitted, and whether we may describe compatibility using the Chartink name with an original logo and clear independent-project wording? If different conditions apply, please point us to them.

## Final acceptance before submitting

- Fresh Chrome profile: install, icon, popup, offline Privacy link, help link and connection instructions all work.
- Connect to localhost and an authorized HTTPS test installation; cancel/deny host permission and recover cleanly. Test IPv6 loopback if advertising it. Broad remote HTTP must remain rejected.
- With persistent Chartink access restricted, test the toolbar capture path. With access allowed, test the scanner-page button. Only supported historical exports should be accepted.
- Capture once, confirm original signals/source in the saved app experiment; retry unchanged history without duplicate records; changed history remains a related experiment.
- Check login interruption, failed connection, pending discard, expiry, browser restart, extension update and successful retry. Confirm previously saved OpenAlgo work remains accessible.
- Confirm import itself starts no broker downloads, calculations or orders. Do not use live strategies or broker credentials to test the extension handoff.
- Run automated capture/bridge tests and package checks. Verify the release ZIP contains no tests, dependency folders, credentials, CSV exports or private screenshots.
- Replace reviewer placeholders with the exact tested app version, source commit and an accessible example scanner. Capture the real screenshots only after this test passes.

Automated checks do not establish Web Store approval, universal broker support or a completed fresh-profile review. Keep any remaining failures visible in release notes rather than promising an untested public experience.
