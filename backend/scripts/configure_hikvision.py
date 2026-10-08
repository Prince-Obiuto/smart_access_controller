"""Provision controller credentials privately using the existing ignored .env."""

import argparse
import getpass
import os
from pathlib import Path
from dotenv import dotenv_values, set_key


def configure(path, prepare=False):
    if path.is_symlink() or not path.is_file():
        raise SystemExit(
            "A regular local .env is required. Existing files are preserved."
        )
    path.chmod(0o600)
    existing = dotenv_values(path)
    defaults = {
        "HIKVISION_HOST": "10.10.20.8",
        "HIKVISION_PORT": "8000",
        "HIKVISION_CONNECT_TIMEOUT": "3",
        "HIKVISION_SDK_DIR": "backend/var/hikvision-sdk",
        "HIKVISION_USERNAME": "",
        "HIKVISION_PASSWORD": "",
    }
    for key, value in defaults.items():
        if key not in existing:
            set_key(path, key, value, quote_mode="always")
    if not prepare:
        if not os.isatty(0):
            raise SystemExit(
                "Use an interactive terminal; credentials must not enter command arguments or logs."
            )
        username = getpass.getpass("Controller username (hidden): ").strip()
        password = getpass.getpass("Controller password (hidden): ")
        if (
            not username
            or not password
            or any(c in username + password for c in "\n\r\x00")
        ):
            raise SystemExit(
                "Non-empty, single-line credentials are required. Existing credentials preserved."
            )
        set_key(path, "HIKVISION_USERNAME", username, quote_mode="always")
        set_key(path, "HIKVISION_PASSWORD", password, quote_mode="always")
    # Explicitly selecting this script prepares the safe mode, never SDK writes.
    set_key(path, "CONTROLLER_MODE", "HIKVISION_READ_ONLY", quote_mode="always")
    set_key(path, "SIMULATION_CONTROLS_ENABLED", "false", quote_mode="always")
    path.chmod(0o600)
    print(
        "Read-only configuration prepared in ignored .env. No credential values displayed. Restart the backend to apply."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare missing keys and safe mode without prompting for credentials.",
    )
    args = parser.parse_args()
    configure(Path(__file__).resolve().parents[2] / ".env", args.prepare)
