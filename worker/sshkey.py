"""Ensure the Azure Linux VM has an SSH key. Run Command is still the control path."""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_ssh_public_key(var_dir: Path) -> str:
    key = var_dir / "ssh" / "id_ed25519"
    public = key.with_suffix(".pub")
    public.parent.mkdir(parents=True, exist_ok=True)
    if not public.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-C", "scylla-dbaas"],
            check=True,
            capture_output=True,
            text=True,
        )
    return public.read_text().strip()
