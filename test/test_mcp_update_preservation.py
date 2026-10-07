"""Preserve the native MCP memory bound while adopting upstream locking."""

from cachetools import TTLCache

import blueprints.mcp_http as mcp_http


def test_token_churn_keeps_quota_buckets_and_hits_bounded(monkeypatch):
    buckets = TTLCache(maxsize=4096, ttl=3600)
    monkeypatch.setattr(mcp_http, "_scope_quota", buckets)
    monkeypatch.setattr(mcp_http, "_last_quota_sweep", 0.0)
    monkeypatch.setattr(mcp_http, "_RATE_LIMIT_READ", "2 per minute")
    for token in range(5000):
        assert mcp_http._within_scope_quota(jti=f"fixture-{token}", scope="read:research")
    assert len(buckets) == 4096
    assert mcp_http._within_scope_quota(jti="fixture-4999", scope="read:research")
    for _ in range(100):
        assert not mcp_http._within_scope_quota(jti="fixture-4999", scope="read:research")
    assert len(buckets["fixture-4999|read:research"]) == 2
