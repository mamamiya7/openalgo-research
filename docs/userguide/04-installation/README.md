# 04 - Installation Guide

## Install OpenAlgo Research

Use the [Research installation guide](../../research/RUNTIME.md#easy-local-installation-recommended) for this repository. It is the canonical guide for the tested release, supported platforms and troubleshooting.

1. Download the named **openalgo-research-0.1.0-preview.6.zip** asset from the [release page](https://github.com/mamamiya7/openalgo-research/releases/tag/research-v0.1.0-preview.6) and extract it into a permanent folder. GitHub's automatic **Source code** downloads do not include the built interface.
2. On Windows x64, double-click **Setup.cmd** and choose your broker. On Linux x86_64, run `bash setup-research.sh`.
3. Complete [first-time setup](../05-first-time-setup/README.md) in the browser that opens.

Setup installs private Python 3.12 and the locked dependencies, creates unique security keys, applies migrations, and starts OpenAlgo and its research worker together. VectorBT and Optuna are included. Initial setup needs internet access; Windows users do not need to install Python, Git or Node.js separately. Linux prerequisites are listed in the installation guide.

For later visits, use **Start.cmd**, or `bash start-research.sh` on Linux. Keep the launcher window open while using the app; press **Ctrl+C** there to stop it.

## Other installation paths

- [Manual installation or managed Linux source installation](../../research/RUNTIME.md) — includes the required research worker and source frontend build.
- [Updates and recovery](../../research/DISTRIBUTION.md#updates-and-recovery) — preserve your configuration, accounts, prices and saved research.
- [Optional Nautilus runtime](../../research/NAUTILUS.md) — separate Linux/WSL environment.
- [Optional Chartink Chrome extension](../../../extensions/chartink/README.md) — CSV upload also works without it.
- [Installation troubleshooting](../../research/RUNTIME.md#if-setup-stops).

Installing from `marketcalls/openalgo` installs upstream OpenAlgo, without this Research distribution. For upstream-only installation, use the [official OpenAlgo documentation](https://docs.openalgo.in/installation-guidelines/getting-started/).

---

**Previous**: [03 - System Requirements](../03-system-requirements/README.md)

**Next**: [05 - First-Time Setup](../05-first-time-setup/README.md)
