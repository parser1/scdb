"""Shared fixtures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.settings import ROOT, Settings
from api.store import Store
from worker.cloud import FakeCloud


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_path=tmp_path / "control.db",
        cloud="fake",
        operator_cidr="203.0.113.5/32",
        repo_root=ROOT,
        var_dir=tmp_path / "var",
        worker_enabled=False,
    )
    store = Store(settings.database_path)
    cloud = FakeCloud()
    app = create_app(settings, store, cloud)
    with TestClient(app) as client:
        yield SimpleNamespace(
            settings=settings,
            store=store,
            cloud=cloud,
            app=app,
            client=client,
            worker=app.state.worker,
            auth={"Authorization": "Bearer test-key"},
        )
