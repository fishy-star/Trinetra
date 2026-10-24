import shutil
import socket

import pytest

from app import registry


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may call the network."""

    def blocked(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def known_fakes(tmp_path, monkeypatch):
    """Every test gets its own copy of known_fakes.json; the real registry is never written."""
    path = tmp_path / "known_fakes.json"
    shutil.copy(registry.KNOWN_FAKES_PATH, path)
    monkeypatch.setattr(registry, "KNOWN_FAKES_PATH", path)
    return path
