"""Explicit per-study notifications using native Slack/Discord text delivery.

No default subscription, order-event impersonation or new sending thread. The
worker commits its attempt before HTTP; uncertain deliveries are not replayed.
The message contains only the outcome and a study link, never names/finances.
"""

import json
import os
import re
import time
from urllib.parse import urlsplit

from sqlalchemy import select

from database.research_db import ResearchJourney, ResearchNotification, ResearchWorker
from research.report_contract import fingerprint

CHANNELS = ("slack", "discord")
TERMINAL = frozenset({"complete", "none", "needs_action"})


def validate(value):
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > 2
        or any(not isinstance(v, str) or v not in CHANNELS for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Choose Slack or Discord once each for research notifications")
    return sorted(value)


def available(channel):
    from database.alert_channels_db import load_channel

    stored = load_channel(channel)
    return bool(stored and stored.enabled and stored.webhook_saved)


def register(db, owner, experiment_id, request_id, channels):
    """Called inside the guarded start transaction; callbacks cannot duplicate it."""
    now = time.time()
    for channel in validate(channels):
        key = fingerprint(["research-notification-v1", owner, experiment_id, request_id, channel])
        if db.get(ResearchNotification, key) is None:
            db.add(
                ResearchNotification(
                    id=key,
                    owner=owner,
                    experiment_id=experiment_id,
                    request_id=request_id,
                    channel=channel,
                    status="watching",
                    created_at=now,
                    updated_at=now,
                )
            )


def status(db, owner, experiment_id, request_id):
    if not request_id:
        return []
    rows = db.scalars(
        select(ResearchNotification)
        .where(
            ResearchNotification.owner == owner,
            ResearchNotification.experiment_id == experiment_id,
            ResearchNotification.request_id == request_id,
        )
        .limit(2)
    )
    return [{"channel": r.channel, "status": r.status, "detail": r.detail} for r in rows]


def _message(identifier, phase):
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("The saved study link is invalid")
    base = urlsplit(os.getenv("HOST_SERVER", "http://127.0.0.1:5000"))
    if base.scheme not in ("http", "https") or not base.netloc or base.username or base.password:
        raise ValueError("Configure a valid OpenAlgo server URL before sending notifications")
    host = f"{base.scheme}://{base.netloc}"
    outcome = {
        "complete": "A historically checked research result is saved.",
        "none": "Research finished. None of the tested setups passed validation.",
        "needs_action": "Research needs your attention.",
    }[phase]
    return f"OpenAlgo Research: {outcome}\n{host}/scanner-research?experiment={identifier}&view=overview"


def send_native(channel, message):
    from services.alerts.notify import notify
    from services.alerts.registry import get_webhook_channel
    from services.alerts.settings import clear_cache

    clear_cache()  # The web process may have changed the switch since our last turn.
    if not available(channel):
        return False
    return notify(get_webhook_channel(channel), message, wait=True).ok


def dispatch_one(store, token, *, stop_requested=None, sender=None, ready=None):
    """One bounded poll/delivery between jobs. No financial allowance is consumed."""
    if stop_requested and stop_requested():
        return False
    with store.sessions() as db:
        candidate = db.scalars(
            select(ResearchNotification.id)
            .where(
                ResearchNotification.status.in_(("watching", "sending")),
            )
            .order_by(ResearchNotification.updated_at, ResearchNotification.id)
            .limit(1)
        ).first()
    if candidate is None:
        return False
    now = time.time()
    with store.sessions.begin() as db:
        from services.scanner_research_service import write_guard

        write_guard(db)
        lease = db.get(ResearchWorker, 1)
        if not lease or lease.token != token or now - lease.heartbeat > 120:
            raise ValueError("Research notifications require the active worker lease")
        row = db.get(ResearchNotification, candidate)
        if row is None or row.status not in ("watching", "sending"):
            return False
        if row.status == "sending":
            if row.worker != token:
                row.status, row.detail = (
                    "uncertain",
                    "Delivery interrupted; not automatically resent.",
                )
                row.updated_at = now
                return True
            return False
        journey = db.get(ResearchJourney, row.experiment_id)
        state = json.loads(journey.state) if journey and journey.owner == row.owner else {}
        row.updated_at = now  # Rotate active studies so one cannot starve others.
        if not journey or state.get("start_request") != row.request_id:
            row.status, row.detail = "cancelled", "The study was removed or a newer run started."
            return True
        if journey.phase not in TERMINAL:
            return False
        try:
            message = _message(row.experiment_id, journey.phase)
            usable = row.channel in CHANNELS and (ready or available)(row.channel)
        except Exception:
            usable = False
        if not usable:
            row.status, row.detail = "not_sent", "Channel unavailable; check its Alerts settings."
            return True
        row.status, row.worker = "sending", token
        identifier, channel = row.id, row.channel
    # No transaction is held across native bounded HTTP. Never retry a call
    # after an exception/timeout or process loss: the provider may have received it.
    try:
        delivered = (sender or send_native)(channel, message)
    except Exception:
        delivered = False
    with store.sessions.begin() as db:
        row = db.get(ResearchNotification, identifier)
        if row.status == "sending" and row.worker == token:
            row.status = "sent" if delivered else "uncertain"
            row.detail = (
                "Notification sent."
                if delivered
                else "Delivery not confirmed; not automatically resent."
            )
            row.updated_at = time.time()
    return True
