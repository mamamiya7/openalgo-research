# Chrome Web Store submission copy

Prepared for 0.1.1. Verify the public URLs and replace the reviewer prerequisites with the actual tested release before submitting. This file is not a published listing.

## Store listing

**Name:** OpenAlgo Research — Chartink

**Short description:** Open Chartink historical scanner signals in your own OpenAlgo Research installation.

**Detailed description:**

Move from a Chartink scanner to a saved research experiment, without manually moving CSV files between apps.

Choose a scanner's historical period, click Research in OpenAlgo, and open its signals in your own OpenAlgo Research installation. The scanner name and source details come with the import so you can return to the same research later.

In the separate OpenAlgo Research app, review your trading rules, run supported backtests, optimize supported settings with Optuna, and save results. The app handles broker prices and calculation engines; this extension handles the scanner import.

What you need:

- Chrome 120 or later.
- A compatible OpenAlgo Research installation supporting Chartink connector protocol 1. Plain OpenAlgo or the older Research preview.4 release alone is insufficient. Follow the linked installation instructions for the tested release.
- A Chartink scanner with an available historical Download → CSV export. Source sign-in and access requirements still apply.

The current research release targets long NSE cash equities. Importing historical signals does not recreate the scanner's formula or make its indicator parameters automatically optimizable.

Your selected export goes to the OpenAlgo address you choose. No broker password or API key is entered into the extension. There are no ads, analytics or publisher-operated import servers. Importing does not place orders or automatically start calculations.

Free and open source. Independent community project; not an official or endorsed Chartink or upstream OpenAlgo product. Separate source-service and broker access requirements still apply.

## Public links

- Homepage/source: https://github.com/mamamiya7/openalgo-research
- Support: https://github.com/mamamiya7/openalgo-research/issues
- Privacy policy: https://github.com/mamamiya7/openalgo-research/blob/main/docs/research/CHARTINK_PRIVACY.md
- Installation: https://github.com/mamamiya7/openalgo-research/blob/main/extensions/chartink/README.md

The new policy and instructions must be pushed before these URLs are used in a submission. Set the publisher contact email in Google's dashboard to an address the owner monitors; no email address has been invented here.

## Privacy practices

**Single purpose:** On a user's request, import a Chartink scanner's official historical signal export into that user's chosen OpenAlgo Research installation as a saved experiment.

| Permission | Explanation for the dashboard |
| --- | --- |
| `activeTab` | Supports user-initiated capture from the toolbar on the active Chartink scanner, including when persistent site access is restricted. |
| `scripting` | Executes bundled export-capture code on the selected Chartink page and a bundled import bridge in the selected OpenAlgo tab. |
| `storage` | Retains the selected connection and latest result locally and one temporary pending import in session storage for interrupted handoffs. |
| `https://chartink.com/*` | Provides the scanner-page import button and access to the historical export requested by the user. The automatic content script matches only `/screener/*`. |
| Optional HTTPS hosts | Users can run OpenAlgo on their own HTTPS domain. The extension requests access to the chosen host when connecting, then restricts delivery to the exact origin and intended tab. It does not request all HTTPS hosts at install time. |
| Optional loopback HTTP hosts | Supports the user's own local OpenAlgo installation at localhost, 127.0.0.1 or IPv6 loopback. Remote HTTP is rejected. Chrome host permissions cover ports, so the bridge additionally checks the exact origin, including port. |

**Data categories suggested by the current implementation:**

- **Website content:** the selected historical CSV and source metadata, including scanner name, dates, symbols, selected-period label and visible repaint notice.
- **Web history:** the selected scanner URL/title, not general browsing-history access or monitoring.

These mappings are an interpretation of Google's categories; confirm the final form matches the implementation. Do not select “no data handled” merely because no publisher server receives imports. The extension does not handle payment details, account balances, broker passwords, cookie values, private messages, health, location or behavioral analytics. Browser-managed same-origin login is not the extension collecting authentication credentials.

**Remote code:** No. The extension executes packaged JavaScript. Its bundled capture function runs in the Chartink page's MAIN world to invoke the site's existing export controls and read inert CSV. No JavaScript is downloaded, evaluated or loaded from a remote script URL by the extension.

Confirm Google's data-use certifications only after checking the final submitted package against the [privacy policy](CHARTINK_PRIVACY.md). Relevant guidance: [Privacy practices](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy), [user-data FAQ](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq), [MV3 requirements](https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements).

## Reviewer instructions — complete these prerequisites before submission

Record the **tested public app release/tag and installation URL**, a **specific accessible Chartink scanner URL with historical CSV export**, and any **dedicated test-account instructions**. Do not submit this template with those details missing. No real trading-account credentials should appear here or in public source.

1. Install the linked compatible OpenAlgo Research release. Start it using its documented setup and create/sign in to a test account. A broker connection is not needed to verify this extension's signal import.
2. Open the extension, enter the test app's address, choose Connect and grant that host access. The app must report Chartink connector protocol 1.
3. Visit the supplied scanner URL, open its historical results and select an available period. Confirm its historical Download → CSV control is available; sign in to the dedicated Chartink test account if necessary.
4. Click Research in OpenAlgo. The app opens an experiment named after the scanner with the original signal export and source details attached.
5. Repeat the same capture. The saved import is reused without duplicate evidence. Changing the exported history creates a related experiment.
6. Open the extension's Privacy link; it works without a network request. Disconnect the test app temporarily to check recovery messaging, then reconnect and use Continue import where offered.

Backtesting/optimization happens in the required app after the import and is outside the extension's single purpose. Broker price downloads, engine trials and orders are not triggered by the import. The extension neither bypasses Chartink access nor supplies a shared scanner dataset.
