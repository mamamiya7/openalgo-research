# Reconcile newer local development into a public package

## October11 development publication

The current development source targets OpenAlgo **2.0.2.7** at reviewed upstream
`12e1114657981a3e50dde8c8ce04eb778ae1cf2f`, with Research preview.8 and qualified
v6 automatic trailing selection. Public preview.7 remains the released download.
The combined native frontend is committed with its matching entrypoints. Private
`.research-runtime/` installer caches remain excluded from Git and Docker context.

The development branch incorporates public installation, onboarding, extension
and release-support files through the reviewed tree reconciliation. The October11
workflow increment joins public main's history while retaining that reviewed
native tree. A tree comparison confirms the only non-asset public source deletions
are the two upstream-superseded Telegram service variants. This resolves ancestry
conflicts without replacing the native host with the older preview.7 tree.
A source-branch push remains separate from merging main or publishing a qualified
installation ZIP; the preview.8 supported-platform matrix is still required.

## Historical October7 development reconciliation

The current reviewed host is `ee26e4e24caeeb68a27a5417bf3744bc0c5e1964`,
declaring OpenAlgo 2.0.2.6, native charts 2.6.0 and OpenScript 0.8.1. Development
metadata targets Research preview.8; public preview.7 remains the published
baseline. The public Setup/Start launchers, desktop supervisor, fresh-install
helper and broker-first configuration journey are restored into the development
source. The newer host's locked atomic configuration writer is retained.

Packaging explicitly retains `openscript_host`, the joint context service and
shared native Values helper. Transient Windows staging locks receive bounded
cleanup retries; a permanent cleanup failure remains visible. Fresh-folder
qualification and populated-app activation are recorded separately from public
supported-platform release evidence. See the [implementation contract](NATIVE_REFINEMENT_20261007.md).

## Historical September30 comparison

Updated 30 September 2026. Preview.7 packages the reviewed public OpenAlgo
**2.0.2.5** source with Historify cache batching and Axios 1.20.0. It is an
installable public increment, not a claim that every feature in the maintainer's
newer local application is shipped.

The newer local application declares OpenAlgo **2.0.2.6** at upstream
`f5165699dfa3925d750c9cf2ef73d586b62611ba`. It also contains reusable entry
conditions, bounded automatic condition screening, final-period admission and
read-only saved trials for unfinished studies. Those changes need reconciliation
with the public installer and onboarding before becoming a versioned package.

## Source reconciliation

Start from a clean checkout of the public repository. Integrate the intended
upstream host revision, then apply reviewed research changes using explicit source
paths and hashes. Never archive or mirror a live installation, and never replace
the public tree with a directory whose Git index still describes an older host.
Keep configuration, accounts, Historify prices, research evidence, local backups
and environment/runtime folders outside the source inventory.

The 30 September comparison found **22 public files absent from the local source**.
Absence is not authorization to delete them. Reconcile these groups deliberately:

| Public files to retain or review | Count | Required treatment |
| --- | ---: | --- |
| Research Setup/Start support, desktop supervisor and fresh-install helper | 5 | Keep `tools/research_setup.ps1`, `research_setup.py`, `research_desktop.py`, `research_desktop_web.py`, `research_install_smoke.py` and their coordinated launcher contract. Existing root launchers must also remain integrated. |
| Installer, desktop, broker-first and lease-read regression files | 6 | Retain their acceptance or replace it with equivalent explicit coverage. |
| Broker credential onboarding component/tests and broker-selection test | 3 | Preserve new-user account and broker-credential setup before OAuth. |
| Chartink in-app guide and its component test | 2 | Preserve the public import/setup entry path. |
| Earlier release notes, Chartink screenshot and extension popup test | 4 | Keep historical release records and extension acceptance. |
| Superseded Telegram service variants | 2 | Check upstream removal and imports; these may be deliberate host cleanup. |

These groups cover the 22 absent public files. The exact reviewed path inventory
controls the merge; root Setup/Start scripts are independently required.

The old local package inventory also omitted new host modules while they were
untracked: agent services, broker helpers, migrations, runtime/thread utilities,
OpenScript entry points and their tests. Update the package allowlist and required
module checks from the committed merged tree. In particular, the `openscript_host`
source root needs explicit admission if the selected host imports it. Do not
solve omissions by allowing every local file or copying runtime directories.

## Qualification before the next combined release

1. Freeze the selected host revision, source inventory, dependency locks, adapters
   and schema migrations. Resolve stale preview metadata and docs in the merged
   source rather than copying contradictory local versions.
2. Verify condition identity through fixed/automatic search, saved trials,
   shortlist/choice, new-CSV reuse, exact replay and final-period admission.
   Incomplete checkpoints must not become winners or completed reports.
3. Build the named ZIP from committed source. Check its manifest and private-data
   exclusions, required host imports and installation files.
4. Install that exact artifact on Windows and Linux; exercise new-user broker
   setup, account login, app/worker lifecycle, Chartink or CSV intake, real engine
   calculations on controlled data, replay, interruption, restart and reinstall.
5. Exercise a populated preview.7 upgrade plus backup/restore and rollback with
   preserved accounts and original result identities. Record broker-specific live
   data checks separately from controlled installation checks.

Versioned releases must state both their public feature scope and tested host.
A local installation receipt, source push or green package-only run cannot replace
missing runtime, migration or broker acceptance.
