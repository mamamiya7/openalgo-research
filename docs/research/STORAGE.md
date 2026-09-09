# Local research storage maintenance

Research metadata lives in `RESEARCH_DATA_DIR/research.db`; immutable evidence
lives in its `artifacts` directory. New evidence is canonical JSON compressed as
`<SHA256>.json.gz`. Legacy `<SHA256>.json` remains readable. The identity hashes
the decoded canonical JSON, so compression does not change recorded identity.

Large values now use a `research-chunks-v1` physical manifest sharing immutable
JSON chunks. The root filename still hashes the exact reconstructed canonical
JSON; API responses, existing identities and exports do not change. Repeated
checkpoints share unchanged prices and receipts instead of copying the entire
accumulated download into every file. Children publish before their parent and
the database checkpoint pointer changes only after publication. Interrupted
writes leave unreferenced files, never a partially published saved checkpoint.
Readers validate every chunk, reconstructed size and root hash, with bounded
depth, node count and expanded bytes. Legacy inline artifacts remain readable.
Logical evidence has a 512 MiB decoded limit; each physical compressed file
retains its 32 MiB limit. The default chunk target is 256 KiB. These bounds do
not establish calculation capacity for every admitted minute workload.

Minute-download recovery additionally uses `minute-acquisition-manifest-v1`.
Each admitted symbol's frozen bars and each raw receipt have their own immutable
artifact, while append-only journals use 256-row segments and a short tail.
Per-job indexes remember their published revisions, so a transport update does
not serialize hundreds of thousands of unchanged prices or reload every older
receipt. Nested `inputs_artifact` references use the same backup/prune closure.
Resume reconstructs the exact prior acquisition state, including receipts even
when disposable sidecars are absent. Legacy cumulative checkpoints still load.
These indexes are bounded by the admitted symbol, bar, receipt and journal
limits and are discarded with the acquisition call.

Use the explicit local maintenance CLI from the project root. Every operation
requires a named store or backup; there is no implicit live-data default.

```powershell
uv run python tools/research_storage.py inspect --store C:/research-local/data
uv run python tools/research_storage.py backup --store C:/research-local/data --destination C:/research-backups/2026-09-06
uv run python tools/research_storage.py restore --backup C:/research-backups/2026-09-06 --destination C:/research-restored/data
uv run python tools/research_storage.py prune --store C:/research-local/data
uv run python tools/research_storage.py prune --store C:/research-local/data --apply
```

These are examples, not directories created by this feature. The destination's
parent must exist, and the named destination must be absent or empty. Backup and
restore stage output in a temporary sibling directory and publish only after
verification. Neither operation overwrites a populated destination. Restore
never extracts ZIP entries or copies unlisted arbitrary files. Select a new
research directory for verification before changing any running configuration.

## Consistency and service state

Stop the calculation worker and web intake before backup or pruning. The tool
does not stop or kill processes. It atomically reserves the research worker row
using a `maintenance:` token, refusing any queued/running/cancelling job or worker
or maintenance lease refreshed within 120 seconds. A stale worker lease does not
make unfinished jobs safe to remove: those jobs still block maintenance and
must first be resolved through ordinary lifecycle recovery/cancellation.

The maintenance token fences research writes through service guards. It renews
during bounded streaming and artifact traversal and releases in a `finally`
block only if still owned by this process. A lost token causes the operation to
fail. Inspection is read-only and may run while work continues; its receipt is
a sampled observation rather than a transactional storage snapshot.

SQLite metadata is copied through SQLite's backup API using the shared
NullPool engine convention. Live SQLite database/WAL files are never copied as
a filesystem snapshot. The backup resets its copied worker lease to inactive;
it does not modify saved job results or restart jobs.

The backup includes all source artifacts, all job result artifacts and every
experiment checkpoint. Recursive `inputs_artifact`, `parent_result_artifact`
and `reference_artifact` references include parent passes and shared
input bundles even when only a checkpoint retains them. Every canonical content
hash is verified, metadata source/job references are checked, and the resulting
closure is verified again after copying. Older populated first-milestone schemas
without experiment/request/history tables are supported; ordinary store
initialization adds missing tables after restoration.

Closure traversal also includes physical chunk references. Backup and pruning
must retain these dependencies even when the decoded object contains no logical
artifact reference. Shared chunks remain while any current source, report or
checkpoint needs them. Neither corruption nor a missing chunk is treated as an
empty successful result.

## Integrity, scope and pruning

Directory backups include a JSON manifest with exact byte counts and SHA256 file
hashes. This detects corruption; it is **not a signature, authentication or
encryption**. Keep backups private and protect their storage. Restore verifies
manifest paths, byte hashes, SQLite integrity, metadata references and canonical
artifact identities before publishing its output. It rejects unfinished jobs,
missing referenced evidence, unsafe paths and symlinked files.

Pruning defaults to a dry run. `--apply` deletes only recognized artifact files
that are outside the complete metadata/input/checkpoint closure and at least one
hour old. All source records are retained; age alone never expires saved work.
Temporary files, unrecognized names, directories and symlinks are reported as
unmanaged entries and are not deleted. Referenced duplicate legacy/compressed
representations are retained conservatively. There is no automatic retention
policy or destructive purge of saved jobs.

`inspect` reports artifact and database bytes, referenced/orphan bytes, unmanaged
entries and the `RESEARCH_QUOTA_MB` receipt (default 2048 MiB). Storage admission
counts every file below the research directory, including acquisition sidecars,
the isolated Historify archive and official extensions. Worker acquisition
progress also checks this budget; an individual bounded write may exceed it
before the next check. Filesystem overhead and
subsequent metadata growth are not a strict filesystem reservation. The maintenance paths are
bounded to 100,000 files/rows, a 32 MiB manifest and a 16 GiB backup; per-artifact
decoded/compressed limits remain enforced by the evidence service.

Acquisition checkpoints and prepared inputs embed exact hash-checked broker
receipts. Missing disposable receipt files are reconstructed from that saved
evidence on resume. Backup preserves calculation and missing-window recovery;
it does not include the mutable Historify database or downloaded official ZIP
directory. Previously completed windows remain in the immutable research input
even when a restored mutable Historify database has no copy of them. Maintain
those optional archives separately if a complete mutable archive is required.
The official baseline remains read-only; configure extensions inside the
isolated research directory, separately from that baseline.

## Local and hosted deployment limits

For a laptop, keep the database and artifact directory together on durable local
storage and back up to a separate private location. Copy a completed backup,
never a running store directory. Restoring into a new directory makes recovery
reviewable without replacing existing evidence.

On Render or another ephemeral host, a local filesystem can disappear during a
restart or redeploy. These maintenance tools do not make ephemeral storage
persistent, arrange an off-host backup, install a worker supervisor or authorize
deployment. A hosted rollout needs durable storage for metadata and artifacts,
a controlled worker lifecycle and a verified private backup/restore procedure.

Tests use only temporary isolated stores. They cover legacy/compressed evidence,
shared checkpoint references, populated old-schema recovery, corrupt/tampered
backup rejection, unsafe paths, nonempty destinations, maintenance fencing,
active lease/job refusal, dry-run versus aged-orphan deletion, quota receipts,
and repeated inspection followed by Windows database rename. SQLite raw
connections, SQLAlchemy sessions/engines, files and staging directories are
released on success and exception paths; no executor or module-level cache is
introduced.
