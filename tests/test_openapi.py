"""OpenAPI snapshot. Regenerate with UPDATE_OPENAPI=1 pytest tests/test_openapi.py"""

import json
import os

from api.app import create_app
from api.settings import ROOT, Settings
from api.store import Store
from worker.cloud import FakeCloud


def test_openapi_snapshot(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_path=tmp_path / "c.db",
        cloud="fake",
        operator_cidr="203.0.113.5/32",
        repo_root=ROOT,
        var_dir=tmp_path / "var",
        worker_enabled=False,
    )
    app = create_app(settings, Store(settings.database_path), FakeCloud())
    schema = app.openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    path = ROOT / "api" / "openapi.json"
    if os.environ.get("UPDATE_OPENAPI"):
        path.write_text(text)
    assert path.read_text() == text
