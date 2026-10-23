"""A scanner larger than the former LRU retains exact causal feature reuse."""

from datetime import datetime, timedelta

from research import stock_features as stock


def test_large_ordered_second_scan_reuses_exact_detached_features(monkeypatch):
    calls = []

    def feature(record, decision, hypothesis):
        calls.append((record, decision, hypothesis))
        return {"value": decision.minute, "nested": {"values": [1, 2]}}, None

    monkeypatch.setattr(stock, "_feature", feature)
    record = {"source": "owned-test-record"}
    bundle = {"series": {"AAA": record}}
    rule = {"id": "controlled", "family": "momentum", "threshold": 0}
    first = datetime.fromisoformat("2026-01-01T09:15:00+05:30")
    clocks = [first + timedelta(days=index) for index in range(1869)]
    owner = stock._PreparedFeatureCache(bundle)
    try:
        expected = []
        for clock in clocks:
            expected.append(owner.resolve(bundle, "AAA", record, clock, rule, feature, "v1"))
        assert len(calls) == len(clocks)
        assert len(owner._PreparedFeatureCache__entries) == len(clocks)
        assert owner._PreparedFeatureCache__bytes <= stock.MAX_PREPARED_FEATURE_BYTES
        for clock, prior in zip(clocks, expected, strict=True):
            actual = owner.resolve(bundle, "AAA", record, clock, rule, feature, "v1")
            assert actual == prior
            actual[0]["nested"]["values"].append("caller mutation")
        assert len(calls) == len(clocks)
        for clock, prior in zip(clocks, expected, strict=True):
            assert owner.resolve(bundle, "AAA", record, clock, rule, feature, "v1") == prior
        assert len(calls) == len(clocks)
    finally:
        owner.close()
    assert owner._PreparedFeatureCache__bytes == 0
    assert not owner._PreparedFeatureCache__entries
