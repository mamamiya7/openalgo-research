"""Durable opt-in lifecycle; no external provider or credentials are used."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select

from database.research_db import (
    ResearchJourney,
    ResearchNotification,
    ResearchStore,
    ResearchWorker,
)
from services import research_notifications as notifications

OWNER, STUDY, REQUEST = "research-owner", "a" * 32, "start-request-1"


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path / "notifications")
    value.initialize()
    with value.sessions.begin() as db:
        row = db.get(ResearchWorker, 1)
        row.token, row.heartbeat = "worker-one", time.time()
        db.add(
            ResearchJourney(
                experiment_id=STUDY,
                owner=OWNER,
                phase="complete",
                state=json.dumps({"start_request": REQUEST}),
                updated_at=time.time(),
            )
        )
    try:
        yield value
    finally:
        value.close()


def register(store, channels=("slack",)):
    with store.sessions.begin() as db:
        notifications.register(db, OWNER, STUDY, REQUEST, list(channels))


def state(store):
    with store.sessions() as db:
        return notifications.status(db, OWNER, STUDY, REQUEST)


def test_default_off_does_not_create_or_send_anything(store):
    register(store, ())
    assert state(store) == []
    assert (
        notifications.dispatch_one(
            store, "worker-one", sender=lambda *a: pytest.fail("Not opted in")
        )
        is False
    )


@pytest.mark.parametrize(
    "bad", [["telegram"], ["slack", "slack"], "slack", [None], ["discord", "slack", "x"]]
)
def test_only_explicit_distinct_supported_webhook_channels(bad):
    with pytest.raises(ValueError):
        notifications.validate(bad)


def test_duplicate_callbacks_create_one_delivery_and_preserve_private_fields(store, monkeypatch):
    monkeypatch.setenv("HOST_SERVER", "https://openalgo.example")
    register(store)
    register(store)
    sent = []
    assert notifications.dispatch_one(
        store,
        "worker-one",
        ready=lambda _: True,
        sender=lambda channel, message: sent.append((channel, message)) or True,
    )
    assert not notifications.dispatch_one(
        store, "worker-one", sender=lambda *a: pytest.fail("Duplicate")
    )
    assert len(sent) == 1 and state(store)[0]["status"] == "sent"
    assert f"experiment={STUDY}&view=overview" in sent[0][1]
    assert OWNER not in sent[0][1] and REQUEST not in sent[0][1]


@pytest.mark.parametrize(
    "phase,phrase",
    [("none", "None of the tested setups"), ("needs_action", "needs your attention")],
)
def test_completion_and_attention_messages_are_truthful(store, phase, phrase):
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, STUDY)
        row.phase = phase
        row.state = json.dumps(
            {"start_request": REQUEST, "error": "PRIVATE_SECRET", "result": {"net_pnl": 12345}}
        )
    register(store)
    sent = []
    notifications.dispatch_one(
        store, "worker-one", ready=lambda _: True, sender=lambda _, text: sent.append(text) or True
    )
    assert phrase in sent[0] and "PRIVATE_SECRET" not in sent[0] and "12345" not in sent[0]


def test_research_keeps_working_while_waiting_for_completion(store):
    with store.sessions.begin() as db:
        db.get(ResearchJourney, STUDY).phase = "researching"
    register(store)
    assert not notifications.dispatch_one(
        store,
        "worker-one",
        ready=lambda _: True,
        sender=lambda *a: pytest.fail("Still researching"),
    )
    assert state(store)[0]["status"] == "watching"


def test_channel_disabled_before_delivery_is_not_sent(store):
    register(store)
    notifications.dispatch_one(
        store,
        "worker-one",
        ready=lambda _: False,
        sender=lambda *a: pytest.fail("Channel disabled"),
    )
    assert state(store)[0]["status"] == "not_sent"


def test_changed_run_does_not_receive_an_old_completion(store):
    register(store)
    with store.sessions.begin() as db:
        db.get(ResearchJourney, STUDY).state = json.dumps({"start_request": "new-request"})
    notifications.dispatch_one(store, "worker-one", sender=lambda *a: pytest.fail("Stale run"))
    assert state(store)[0]["status"] == "cancelled"


def test_restart_after_uncertain_send_does_not_resend(store):
    register(store)
    with store.sessions.begin() as db:
        row = db.scalars(select(ResearchNotification)).first()
        row.status, row.worker = "sending", "previous-worker"
    notifications.dispatch_one(
        store, "worker-one", sender=lambda *a: pytest.fail("Uncertain duplicate")
    )
    assert state(store)[0]["status"] == "uncertain"


def test_provider_timeout_is_terminal_and_never_retried(store):
    register(store)
    calls = []

    def timeout(*args):
        calls.append(args)
        raise TimeoutError("PRIVATE_PROVIDER_URL")

    notifications.dispatch_one(store, "worker-one", ready=lambda _: True, sender=timeout)
    notifications.dispatch_one(
        store, "worker-one", sender=lambda *a: pytest.fail("No automatic retry")
    )
    assert len(calls) == 1 and state(store)[0]["status"] == "uncertain"
    assert "PRIVATE_PROVIDER_URL" not in str(state(store))


def test_concurrent_worker_callbacks_cannot_post_twice(store):
    register(store)
    entered, release = Event(), Event()
    calls = []

    def sender(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            notifications.dispatch_one, store, "worker-one", ready=lambda _: True, sender=sender
        )
        assert entered.wait(5)
        assert not notifications.dispatch_one(
            store, "worker-one", ready=lambda _: True, sender=sender
        )
        release.set()
        assert first.result(5)
    assert len(calls) == 1 and state(store)[0]["status"] == "sent"


def test_owner_and_lease_are_required(store):
    register(store)
    with pytest.raises(ValueError, match="lease"):
        notifications.dispatch_one(store, "wrong-worker")
    with store.sessions() as db:
        assert notifications.status(db, "someone-else", STUDY, REQUEST) == []
        assert db.scalar(select(func.count()).select_from(ResearchNotification)) == 1
