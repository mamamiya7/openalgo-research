"""Job-local immutable candidate histories; checkpoints contain references only."""

from copy import deepcopy

from research import development_evidence as contract
from research.report_contract import fingerprint
from services.scanner_research_service import encoded, read_artifact, save_artifact

VERSION = "research-development-evidence-manifest-v1"
MAX_INDEX_BYTES = 512 * 1024


class DevelopmentEvidenceWriter:
    def __init__(self, store, evidence, *, binding, max_evaluations, saved=None):
        self.store = store
        self.binding = deepcopy(binding)
        self.plan = contract.plan(evidence, max_evaluations)
        self.entries = {}
        self.marks = 0
        self.bytes = 0
        if saved is not None:
            self._restore(saved)

    def _restore(self, saved):
        if (
            not isinstance(saved, dict)
            or len(encoded(saved)) > MAX_INDEX_BYTES
            or saved.get("version") != VERSION
            or saved.get("id") != fingerprint({k: v for k, v in saved.items() if k != "id"})
            or saved.get("binding") != self.binding
            or saved.get("plan") != self.plan
            or saved.get("status") not in ("collecting", "complete", "partial", "unavailable")
        ):
            raise ValueError("The saved development evidence manifest changed")
        entries = saved.get("entries")
        if not isinstance(entries, dict) or len(entries) > self.plan.get(
            "max_evaluations",
            self.plan.get("limits", {}).get("max_evaluations", contract.MAX_EVALUATIONS),
        ):
            raise ValueError("The saved development evidence index exceeds its bound")
        declared_marks = declared_bytes = 0
        for key, descriptor in entries.items():
            if (
                not isinstance(descriptor, dict)
                or descriptor.get("evaluation_id") != key
                or descriptor.get("binding") != self.binding
                or descriptor.get("period") in (None, "final", "evaluation")
                or descriptor.get("status") not in ("retained", "unavailable")
            ):
                raise ValueError("The saved development evidence entry changed")
            if descriptor["status"] == "retained":
                if self.plan["status"] != "available":
                    raise ValueError("The saved development evidence was not admitted")
                marks, size = descriptor.get("marks"), descriptor.get("bytes")
                if (
                    type(marks) is not int
                    or not 1 <= marks <= contract.MAX_MARKS_PER_EVALUATION
                    or type(size) is not int
                    or not 1 <= size <= contract.MAX_SHARD_BYTES
                ):
                    raise ValueError("The saved development evidence quantities exceed their bound")
                declared_marks += marks
                declared_bytes += size
                if (
                    declared_marks > contract.MAX_TOTAL_MARKS
                    or declared_bytes > contract.MAX_TOTAL_BYTES
                ):
                    raise ValueError("The saved development evidence totals exceed their bound")
        if saved.get("marks") != declared_marks or saved.get("bytes") != declared_bytes:
            raise ValueError("The saved development evidence totals changed")
        # Admit the complete small index before opening any of its shards.
        for key, descriptor in entries.items():
            if descriptor["status"] == "retained":
                # One bounded shard at a time. No account-history cache survives
                # this verification or grows with the number of candidates.
                shard = read_artifact(
                    self.store,
                    descriptor.get("inputs_artifact"),
                    max_bytes=contract.MAX_SHARD_BYTES,
                )
                contract.verify_shard(shard, descriptor=descriptor)
                if (
                    descriptor.get("basis_id") != fingerprint(shard["evaluation_basis"])
                    or descriptor.get("hypothesis_id") != fingerprint(shard["hypothesis"])
                    or descriptor.get("condition_id")
                    != (
                        fingerprint(shard["stock_condition"])
                        if "stock_condition" in shard
                        else None
                    )
                ):
                    raise ValueError("The saved development evidence context changed")
                marks, size = len(shard["timeline"]), len(encoded(shard))
                if descriptor.get("marks") != marks or descriptor.get("bytes") != size:
                    raise ValueError("The saved development evidence quantities changed")
                self.marks += marks
                self.bytes += size
                self._check_totals()
                del shard
            self.entries[key] = deepcopy(descriptor)
        self._check_totals()
        if saved.get("marks") != self.marks or saved.get("bytes") != self.bytes:
            raise ValueError("The saved development evidence totals changed")

    def _check_totals(self):
        if self.marks > contract.MAX_TOTAL_MARKS or self.bytes > contract.MAX_TOTAL_BYTES:
            raise ValueError("Development evidence exceeded its recorded storage bound")

    def retain(self, report, *, search_identity, **context):
        if search_identity != self.binding["search_identity"]:
            raise ValueError("Development evidence belongs to a different search")
        key = context["evaluation_id"]
        if key not in self.entries and len(self.entries) >= self.plan.get(
            "max_evaluations",
            self.plan.get("limits", {}).get("max_evaluations", contract.MAX_EVALUATIONS),
        ):
            raise ValueError("Development evidence exceeded its evaluation bound")
        descriptor = {
            "evaluation_id": key,
            "binding": deepcopy(self.binding),
            "period": context["period"],
            "stress": context["stress"],
            "basis_id": fingerprint(report.get("evaluation_basis")),
            "hypothesis_id": fingerprint(context["hypothesis"]),
            "condition_id": (
                fingerprint(report["automatic_stock_condition"])
                if "automatic_stock_condition" in report
                else None
            ),
        }
        if descriptor["period"] in ("final", "evaluation"):
            raise ValueError("The reserved final period is not development evidence")
        shard = None
        if self.plan["status"] == "available":
            try:
                shard = contract.build(report, binding=self.binding, **context)
            except ValueError:
                # Preserve the original calculation contract. An unavailable
                # history never qualifies as complete statistical evidence.
                descriptor.update(status="unavailable", reason="unverified_account_history")
        else:
            descriptor.update(status="unavailable", reason=self.plan["reason"])
        if shard is not None:
            marks, size = len(shard["timeline"]), len(encoded(shard))
            previous = self.entries.get(key, {})
            if (
                self.marks - previous.get("marks", 0) + marks > contract.MAX_TOTAL_MARKS
                or self.bytes - previous.get("bytes", 0) + size > contract.MAX_TOTAL_BYTES
            ):
                raise ValueError("Development evidence exceeded its admitted storage bound")
            descriptor.update(
                **{k: shard[k] for k in ("id", "config_id", "evaluation_basis_id")},
                status="retained",
                marks=marks,
                bytes=size,
            )
            # Publish completely before the checkpoint references this shard.
            # Native artifact publication releases its lease/SQL connection
            # before the caller persists the checkpoint.
            descriptor["inputs_artifact"] = save_artifact(self.store, shard)
        previous = self.entries.get(key)
        if previous is not None and previous != descriptor:
            raise ValueError("A recorded development evaluation changed")
        if previous is None:
            self.entries[key] = descriptor
            self.marks += descriptor.get("marks", 0)
            self.bytes += descriptor.get("bytes", 0)
        return deepcopy(descriptor)

    def reconcile(self, evaluations):
        expected = {}
        for key, row in evaluations.items():
            if row.get("evaluation_basis", {}).get("period", {}).get("kind") == "evaluation":
                continue
            descriptor = row.get("development_evidence")
            if not isinstance(descriptor, dict) or descriptor.get("basis_id") != fingerprint(
                row.get("evaluation_basis")
            ):
                raise ValueError("Development evidence and calculation basis disagree")
            if descriptor.get("condition_id") is not None:
                condition = row.get("automatic_stock_condition")
                if (
                    not isinstance(condition, dict)
                    or descriptor["condition_id"] != fingerprint(condition)
                    or descriptor["hypothesis_id"] != fingerprint(condition.get("hypothesis"))
                ):
                    raise ValueError("Development evidence and calculation condition disagree")
            expected[key] = descriptor
        if expected != self.entries:
            raise ValueError("Development evidence and calculation checkpoint disagree")

    def snapshot(self, *, complete=False):
        status = "complete" if complete else "collecting"
        if self.plan["status"] != "available":
            status = "unavailable"
        elif any(row["status"] != "retained" for row in self.entries.values()):
            status = "partial"
        manifest = {
            "version": VERSION,
            "binding": deepcopy(self.binding),
            "plan": deepcopy(self.plan),
            "status": status,
            "entries": deepcopy(self.entries),
            "marks": self.marks,
            "bytes": self.bytes,
            "reserved_final_excluded": True,
        }
        manifest["id"] = fingerprint(manifest)
        return manifest
