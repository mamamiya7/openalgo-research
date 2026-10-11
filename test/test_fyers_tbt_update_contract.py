"""Fyers September stream fixes, without connecting to an account or socket."""

from types import SimpleNamespace

from broker.fyers.streaming import fyers_tbt_websocket as tbt


class Fields:
    def __init__(self, **values):
        self.__dict__.update(values)

    def HasField(self, name):
        return name in self.__dict__


def level(index, price, quantity):
    return Fields(
        num=SimpleNamespace(value=index),
        price=SimpleNamespace(value=price),
        qty=SimpleNamespace(value=quantity),
        nord=SimpleNamespace(value=1),
    )


def feed(*bids, asks=()):
    return Fields(depth=Fields(bids=list(bids), asks=list(asks)))


def client(monkeypatch):
    monkeypatch.setattr(tbt.FyersTbtWebSocket, "_get_tbt_url", lambda self: "wss://test.invalid")
    return tbt.FyersTbtWebSocket("example-app:example-token")


def test_socket_keeps_app_id_and_access_token(monkeypatch):
    instance = client(monkeypatch)
    headers = []

    class Socket:
        def __init__(self, url, **kwargs):
            headers.append(kwargs["header"])

        def run_forever(self, **kwargs):
            instance.running = False

    monkeypatch.setattr(tbt.websocket, "WebSocketApp", Socket)
    instance.running = True
    instance._run_websocket()
    assert headers == [{"authorization": "example-app:example-token"}]


def test_sparse_diff_updates_real_level_and_zero_clears(monkeypatch):
    instance = client(monkeypatch)
    instance._extract_depth("SBIN", feed(level(0, 10000, 10), level(9, 9900, 7)), True)
    instance._extract_depth("SBIN", feed(level(9, 9800, 8)), False)
    state = instance.depth_data["SBIN"]
    assert state["buy"][0]["price"] == 100
    assert state["buy"][9] == {"price": 98, "quantity": 8, "orders": 1}
    instance._extract_depth("SBIN", feed(level(9, 0, 0)), False)
    assert state["buy"][9]["price"] == 0
    assert state["buy"][9]["quantity"] == 0


def test_snapshot_removes_stale_levels(monkeypatch):
    instance = client(monkeypatch)
    instance._extract_depth("SBIN", feed(level(9, 9900, 7), asks=[level(7, 10100, 9)]), True)
    instance._extract_depth("SBIN", feed(level(0, 10000, 10)), True)
    state = instance.depth_data["SBIN"]
    assert state["buy"][0]["quantity"] == 10
    assert state["buy"][9] == {"price": 0, "quantity": 0, "orders": 0}
    assert state["sell"][7] == {"price": 0, "quantity": 0, "orders": 0}
