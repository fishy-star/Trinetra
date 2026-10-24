import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may call the network."""

    def blocked(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
