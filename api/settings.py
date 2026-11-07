"""Process settings. The Azure adapter stays off unless CLOUD=azure."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    api_key: str
    database_path: Path
    cloud: str
    operator_cidr: str
    repo_root: Path
    var_dir: Path
    worker_enabled: bool = True
    location_default: str = "eastus"
    zone: str = "1"

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("API_KEY", "")
        if not api_key:
            raise SystemExit("Set API_KEY before starting the control plane.")
        root = Path(os.environ.get("REPO_ROOT", ROOT))
        var_dir = Path(os.environ.get("VAR_DIR", root / "var"))
        database = Path(os.environ.get("DATABASE_PATH", var_dir / "control.db"))
        cloud = os.environ.get("CLOUD", "fake").strip().lower()
        if cloud not in {"fake", "azure"}:
            raise SystemExit("CLOUD must be fake or azure.")
        return cls(
            api_key=api_key,
            database_path=database,
            cloud=cloud,
            operator_cidr=os.environ.get("OPERATOR_CIDR", "").strip(),
            repo_root=root,
            var_dir=var_dir,
            worker_enabled=os.environ.get("WORKER_ENABLED", "1") != "0",
            location_default=os.environ.get("AZURE_LOCATION", "eastus"),
            zone=os.environ.get("AZURE_ZONE", "1"),
        )
