"""Uvicorn entrypoint. Set API_KEY and, for a real subscription, CLOUD=azure."""

from __future__ import annotations

import logging

from api.app import create_app
from api.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = create_app(Settings.from_env())
