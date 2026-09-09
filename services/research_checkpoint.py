"""Incremental recovery manifests for append-only, frozen minute acquisition."""

from research.data import MAX_BARS
from services.research_acquisition import acquisition_receipts
from services.scanner_research_service import encoded, read_artifact, save_artifact

VERSION = "minute-acquisition-manifest-v1"
PART_SIZE = 256
LIST_FIELDS = ("receipts", "findings", "transport_attempts", "completed_windows")


def unpack_checkpoint(store, saved):
    """Old inline checkpoints and new manifests produce identical acquisition state."""
    if not saved or "acquisition_manifest" not in saved:
        return saved
    manifest = saved["acquisition_manifest"]
    if manifest.get("version") != VERSION:
        raise ValueError("Unsupported download recovery format")
    state = dict(manifest["metadata"])
    state["bars"], state["raw_bars"] = {}, {}
    total_prices = 0
    if len(manifest["symbols"]) > 25000:
        raise ValueError("Download recovery exceeds the symbol limit")
    for symbol, item in manifest["symbols"].items():
        if type(item.get("count")) is not int or item["count"] < 0:
            raise ValueError("Invalid download recovery price count")
        total_prices += item["count"]
        if total_prices > MAX_BARS:
            raise ValueError("Download recovery exceeds the price limit")
        data = read_artifact(store, item["inputs_artifact"])
        if len(data["bars"]) != item["count"] or len(data["raw_bars"]) != item["count"]:
            raise ValueError("Downloaded price recovery failed its count check")
        state["bars"][symbol], state["raw_bars"][symbol] = data["bars"], data["raw_bars"]
    for field in LIST_FIELDS:
        item = manifest["lists"][field]
        limit = 1000000 if field == "findings" else 100000
        if (
            type(item.get("count")) is not int
            or not 0 <= item["count"] <= limit
            or len(item["parts"]) != item["count"] // PART_SIZE
        ):
            raise ValueError("Download recovery exceeds the journal limit")
        values = []
        for part in item["parts"]:
            rows = read_artifact(store, part["inputs_artifact"])
            if not isinstance(rows, list) or len(rows) != PART_SIZE:
                raise ValueError("Invalid download recovery segment")
            values.extend(rows)
        if not isinstance(item["tail"], list) or len(item["tail"]) >= PART_SIZE:
            raise ValueError("Invalid download recovery tail")
        values.extend(item["tail"])
        if len(values) != item["count"]:
            raise ValueError("Download recovery failed its count check")
        state[field] = values
    receipts, receipt_bytes = [], 0
    if len(manifest["receipt_artifacts"]) > 100000:
        raise ValueError("Download recovery exceeds the receipt limit")
    for row in manifest["receipt_artifacts"]:
        receipt = read_artifact(store, row["inputs_artifact"])
        receipt_bytes += len(encoded(receipt))
        if receipt_bytes > 128 * 1024**2:
            raise ValueError("Download recovery exceeds the receipt size limit")
        receipts.append({"sha256": row["inputs_artifact"], "receipt": receipt})
    if [item["sha256"] for item in receipts] != [item["sha256"] for item in state["receipts"]]:
        raise ValueError("Download recovery receipt references do not match")
    return {
        "reference_artifact": saved["reference_artifact"],
        "acquisition": state,
        "acquisition_receipts": receipts,
    }


class MinuteCheckpointWriter:
    """Job-local indexes only; all price and receipt payloads stay in immutable files.

    Minute admission never rewrites an admitted timestamp. Length is therefore a
    revision for each symbol, and the four journal lists only append in a pass.
    No index escapes the bounded acquisition or survives its owner job.
    """

    def __init__(self, store, receipts_dir, saved=None):
        self.store, self.receipts_dir = store, receipts_dir
        previous = (saved or {}).get("acquisition_manifest", {})
        self.symbols = dict(previous.get("symbols", {}))
        self.parts = {
            field: list(previous.get("lists", {}).get(field, {}).get("parts", []))
            for field in LIST_FIELDS
        }
        self.receipts = {
            item["inputs_artifact"]: item for item in previous.get("receipt_artifacts", [])
        }

    def pack(self, state):
        symbols = {}
        for symbol, bars in state["bars"].items():
            count = len(bars)
            previous = self.symbols.get(symbol)
            if previous and previous["count"] > count:
                raise ValueError("Frozen downloaded prices cannot shrink during recovery")
            if previous is None or previous["count"] != count:
                digest = save_artifact(
                    self.store, {"bars": bars, "raw_bars": state["raw_bars"][symbol]}
                )
                previous = {"inputs_artifact": digest, "count": count}
                self.symbols[symbol] = previous
            symbols[symbol] = previous
        lists = {}
        for field in LIST_FIELDS:
            rows = state.get(field, [])
            parts = self.parts[field]
            if len(rows) < len(parts) * PART_SIZE:
                raise ValueError("Download recovery journal cannot shrink")
            while (len(parts) + 1) * PART_SIZE <= len(rows):
                start = len(parts) * PART_SIZE
                parts.append(
                    {"inputs_artifact": save_artifact(self.store, rows[start : start + PART_SIZE])}
                )
            lists[field] = {
                "parts": list(parts),
                "tail": rows[len(parts) * PART_SIZE :],
                "count": len(rows),
            }
        receipt_artifacts = []
        for item in state["receipts"]:
            digest = item["sha256"]
            if digest not in self.receipts:
                raw = acquisition_receipts(
                    {"provenance": {"broker_receipts": [item]}}, self.receipts_dir
                )[0]
                if save_artifact(self.store, raw["receipt"]) != digest:
                    raise ValueError("Download receipt identity changed during recovery")
                self.receipts[digest] = {"inputs_artifact": digest}
            receipt_artifacts.append(self.receipts[digest])
        return {
            "version": VERSION,
            "metadata": {
                key: value
                for key, value in state.items()
                if key not in {"bars", "raw_bars", *LIST_FIELDS}
            },
            "symbols": symbols,
            "lists": lists,
            "receipt_artifacts": receipt_artifacts,
        }
