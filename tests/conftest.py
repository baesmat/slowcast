"""Shared fixtures: a temp-configured app with the pipeline stubbed out."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slowcast.config import Config
from slowcast.web import create_app


def make_cfg(tmp_path: Path, **overrides) -> Config:
    cfg = Config(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
        token="testtoken",
        feed_token="feedtoken123",
        public_url="http://testserver",
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_client(tmp_path: Path, **cfg_overrides) -> TestClient:  # type: ignore[valid-type]
    cfg = make_cfg(tmp_path, **cfg_overrides)
    return TestClient(create_app(cfg))


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:  # type: ignore[valid-type]
    with make_client(tmp_path) as c:
        yield c


def auth(client: TestClient) -> None:  # type: ignore[valid-type]
    """Set the session cookie the way the login form does."""
    client.cookies.set("sc_token", "testtoken")
