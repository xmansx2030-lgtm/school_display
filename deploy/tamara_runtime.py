"""Install this host's existing Tamara live credentials for School Display only.

Run as root on the shared production host. No token is accepted on argv or
printed, and the source application's environment is never modified.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path


SOURCE = Path("/opt/school_reports/deploy/hetzner/env.production")
TARGET = Path("/opt/school-display/app/.env.production")
TOKEN_KEYS = ("TAMARA_API_TOKEN", "TAMARA_NOTIFICATION_TOKEN")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._+/=-]{20,}")


def parse_env(contents: str) -> dict[str, str]:
    values = {}
    for line in contents.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def updated_env(contents: str, updates: dict[str, str]) -> str:
    lines = []
    seen = set()
    for line in contents.splitlines():
        match = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)\s*=", line)
        if match and match.group(2) in updates:
            key = match.group(2)
            if key not in seen:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def apply(mode: str, backup: Path) -> None:
    if mode not in {"prepare", "activate"}:
        raise RuntimeError("Invalid Tamara runtime mode")
    if not SOURCE.is_file() or not TARGET.is_file():
        raise RuntimeError("Both production environment files must exist")
    if backup.parent != TARGET.parent or not backup.name.startswith(".env.production.bak.tamara-"):
        raise RuntimeError("Backup must be a named file beside the School Display environment")
    if backup.exists():
        raise RuntimeError("Backup already exists; refusing to overwrite it")

    source = parse_env(SOURCE.read_text(encoding="utf-8-sig"))
    target_contents = TARGET.read_text(encoding="utf-8-sig")
    target = parse_env(target_contents)
    if source.get("TAMARA_ENABLED", "").lower() != "true" or source.get("TAMARA_ENVIRONMENT") != "production":
        raise RuntimeError("The source Tamara integration is not enabled in production")
    if any(not TOKEN_PATTERN.fullmatch(source.get(key, "")) for key in TOKEN_KEYS):
        raise RuntimeError("The source is missing a valid-looking Tamara credential")

    if mode == "activate":
        if any(target.get(key) != source[key] for key in TOKEN_KEYS):
            raise RuntimeError("Prepare the live credentials before activation")
        if target.get("TAMARA_ENVIRONMENT") != "production":
            raise RuntimeError("The School Display environment is not production")

    updates = {
        "TAMARA_ENABLED": "True" if mode == "activate" else "False",
        "TAMARA_ENVIRONMENT": "production",
        "TAMARA_API_BASE_URL": "https://api.tamara.co",
        "TAMARA_CALLBACK_BASE_URL": "https://school-display.com",
        "TAMARA_API_TOKEN": source["TAMARA_API_TOKEN"],
        "TAMARA_NOTIFICATION_TOKEN": source["TAMARA_NOTIFICATION_TOKEN"],
    }
    os.umask(0o077)
    shutil.copyfile(TARGET, backup)
    backup.chmod(0o600)
    descriptor, staged_name = tempfile.mkstemp(prefix=".env.production.tamara-", dir=TARGET.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as staged:
            staged.write(updated_env(target_contents, updates))
        os.chmod(staged_name, 0o600)
        os.replace(staged_name, TARGET)
    finally:
        if os.path.exists(staged_name):
            os.unlink(staged_name)
    print(f"Tamara {mode} complete; source untouched; backup created")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("prepare", "activate"), required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    apply(args.mode, args.backup)
