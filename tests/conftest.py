import socket

import pytest

from app import main


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may call the network."""

    def blocked(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def no_runtime_database():
    """The web page's lookups would open DATA_DIR's real database; tests use none."""
    main.app.dependency_overrides[main.web_store] = lambda: None
    yield
    main.app.dependency_overrides.pop(main.web_store, None)
