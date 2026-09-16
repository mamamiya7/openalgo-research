# 05 - First-Time Setup

These steps follow the [OpenAlgo Research preview.6 installer](../../research/RUNTIME.md#easy-local-installation-recommended). Setup starts the app and research worker and opens your browser when both are ready.

## Create your account and connect your broker

1. Create your OpenAlgo account in the browser, then sign in. If you already have an account in this installation, sign in with it.
2. On **Connect Broker**, select **Add broker credentials**. Enter the app credentials issued by your broker and save. Use the field hints and your broker's developer portal for its required callback URL and credential format.
3. Stop the launcher with **Ctrl+C**, then reopen **Start.cmd** on Windows or run `bash start-research.sh` on Linux. Restarting loads the saved credentials.
4. Sign in and complete your broker's connection flow.
5. Open **Tools → Backtest & Optimize** and choose how to start below.

Later credential changes are available under **Profile → Broker Credentials** and also require a restart. See [Broker Connection](../06-broker-connection/README.md) for adapter-specific details.

## Start your first research setup

- **From Chartink:** Choose **Import from Chartink** for the installation guide, or follow the [Chrome extension instructions](../../../extensions/chartink/README.md#install-the-extension). Pin the extension and connect it to the same OpenAlgo address you use to sign in. On a Chartink scanner, choose the historical backtest period with **Download → CSV** available, then click **Research in OpenAlgo**. Your named setup opens with the signals attached.
- **From a file:** In **Tools → Backtest & Optimize**, create a setup and upload your dated signal CSV. No extension is needed.

Review the imported dates, capital and trading settings, then choose **Backtest** or **Optimize**. Importing only saves a draft. OpenAlgo reuses Historify prices and downloads missing required candles through your connected broker when you run. Reopen saved work from the Research library.

## Settings and saved data

Setup creates unique security keys automatically and preserves an existing `.env`. **Do not regenerate existing keys or replace `.env` when updating**; keep them with your private data backup. There is no manual key-generation step for the guided installer.

VectorBT and Optuna are already included. The [Chartink extension](../../../extensions/chartink/README.md) and [Nautilus runtime](../../research/NAUTILUS.md) are optional additions.

For later visits, use the same launcher. For setup failures, see [installation troubleshooting](../../research/RUNTIME.md#if-setup-stops). Before upgrading, follow [updates and recovery](../../research/DISTRIBUTION.md#updates-and-recovery).

---

**Previous**: [04 - Installation Guide](../04-installation/README.md)

**Next**: [06 - Broker Connection](../06-broker-connection/README.md)
