"""Bounded read-only SDK identification, capability and event diagnostics."""

import argparse
import ipaddress
import json
import platform
import socket
import struct
import subprocess
from datetime import datetime, timedelta, timezone
import time
from pathlib import Path

from pydantic import ValidationError
from .config import ControllerSettings


def result(status, code, **details):
    return {"status": status, "code": code, **details}


def tcp_probe(settings):
    # A literal IPv4 address avoids an unbounded DNS lookup before socket timeout.
    try:
        address = str(ipaddress.IPv4Address(settings.hikvision_host))
    except ipaddress.AddressValueError:
        return result("FAILED", "HOST_MUST_BE_IPV4")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(settings.hikvision_connect_timeout)
            connection.connect((address, settings.hikvision_port))
        return result("VERIFIED", "TCP_CONNECTED")
    except TimeoutError:
        return result("FAILED", "TCP_TIMEOUT")
    except PermissionError:
        return result("FAILED", "TCP_PERMISSION_DENIED")
    except OSError:
        # Do not render arbitrary OS/native error text or configuration values.
        return result("FAILED", "TCP_CONNECTION_FAILED")


def inspect_sdk(directory):
    """Inspect files without executing a vendor library (or unsafe ldd)."""
    directory = Path(directory)
    if not directory.is_dir():
        return result("BLOCKED", "SDK_PACKAGE_MISSING")
    # Fixed relative names only: obtain the vendor distribution, do not search
    # unrelated private directories or accept a random system library as proof.
    headers = [
        directory / p
        for p in (
            "incEn/HCNetSDK.h",
            "include/HCNetSDK.h",
            "Inc/HCNetSDK.h",
            "HCNetSDK.h",
        )
    ]
    libraries = [
        directory / p
        for p in ("lib/libhcnetsdk.so", "Libs/libhcnetsdk.so", "libhcnetsdk.so")
    ]
    header = next((p for p in headers if p.is_file()), None)
    library = next((p for p in libraries if p.is_file()), None)
    if not header or not library:
        return result(
            "BLOCKED",
            "SDK_HEADERS_OR_LIBRARY_MISSING",
            headerPresent=bool(header),
            libraryPresent=bool(library),
        )
    try:
        with library.open("rb") as file:
            elf = file.read(20)
        if len(elf) < 20 or elf[:4] != b"\x7fELF" or elf[5] not in (1, 2):
            return result("FAILED", "SDK_NOT_ELF")
        machine = struct.unpack("<H" if elf[5] == 1 else ">H", elf[18:20])[0]
        expected = {"x86_64": (2, 62), "aarch64": (2, 183), "i686": (1, 3)}.get(
            platform.machine()
        )
        if expected != (elf[4], machine):
            return result("FAILED", "SDK_ARCHITECTURE_MISMATCH")
        # readelf parses metadata; it does not load the vendor's code.
        try:
            read = subprocess.run(
                ["readelf", "-d", str(library)],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except FileNotFoundError:
            read = None
        dependencies = []
        if read and read.returncode == 0:
            import re

            dependencies = re.findall(
                r"\(NEEDED\).*?\[([A-Za-z0-9_.+-]+)\]",
                read.stdout.decode("utf-8", errors="replace"),
            )
        return result(
            "VERIFIED",
            "SDK_ELF_ARCHITECTURE_MATCHED",
            architecture="MATCHED",
            declaredDependencies=dependencies,
            dependencyInspection="READ"
            if read and read.returncode == 0
            else "UNAVAILABLE",
            runtimeDependencies="UNKNOWN",
            sdkVersion="UNKNOWN",
            licensing="UNKNOWN",
        )
    except FileNotFoundError:
        return result("BLOCKED", "SDK_DEPENDENCY_INSPECTOR_MISSING")
    except (OSError, subprocess.TimeoutExpired):
        return result("FAILED", "SDK_INSPECTION_FAILED")


def diagnose(settings, tcp_only=False, sample_limit=20, listen_seconds=0):
    credentials = bool(settings.hikvision_username.get_secret_value().strip()) and bool(
        settings.hikvision_password.get_secret_value()
    )
    checks = {
        "configuration": result(
            "VERIFIED" if credentials else "BLOCKED",
            "CREDENTIALS_PRESENT" if credentials else "CREDENTIALS_MISSING",
        ),
        "tcp": tcp_probe(settings),
    }
    native = None
    if not tcp_only:
        checks["sdk"] = inspect_sdk(settings.hikvision_sdk_dir)
        blocker = (
            "CREDENTIALS_MISSING"
            if not credentials
            else "TCP_NOT_CONNECTED"
            if checks["tcp"]["status"] != "VERIFIED"
            else "SDK_TRANSPORT_NOT_VERIFIED"
        )
        checks["login"] = result("BLOCKED", blocker, attempts=0)
        for stage in (
            "model",
            "firmware",
            "deviceTime",
            "accessCapabilities",
            "historicalEvents",
            "liveEvents",
        ):
            checks[stage] = result("BLOCKED", "LOGIN_NOT_ESTABLISHED")
        checks["historicalEvents"]["requestedSampleLimit"] = sample_limit
        checks["cleanup"] = result("VERIFIED", "NO_SDK_SESSION_CREATED")
        if credentials and checks["sdk"].get("architecture") == "MATCHED":
            from .hikvision import NativeSession
            from .controllers import ControllerReadUnavailable

            native = NativeSession(settings)
            try:
                native.start()
                end = datetime.now(timezone.utc)
                events, complete = native.read_history(
                    end - timedelta(minutes=settings.hikvision_initial_minutes),
                    end,
                    sample_limit,
                )
                checks["historicalEvents"] = {
                    **native.metadata.get("history", {}),
                    "count": len(events),
                    "complete": complete,
                    "sample": [
                        {
                            "at": e["at"].isoformat() if e["at"] else None,
                            "card": e["card"],
                            "reader": e["device_reader"],
                            "door": e["device_door"],
                            "outcome": e["result"],
                            "flags": e["flags"],
                        }
                        for e in events
                    ],
                }
                deadline = time.monotonic() + listen_seconds
                while time.monotonic() < deadline:
                    native.poll_live()
                    time.sleep(0.1)
                live = native.poll_live()
                checks["liveEvents"] = {
                    **native.metadata["stages"].get("subscription", {}),
                    "count": len(live),
                    "sample": [
                        {
                            "at": e["at"].isoformat() if e["at"] else None,
                            "card": e["card"],
                            "reader": e["device_reader"],
                            "door": e["device_door"],
                            "outcome": e["result"],
                            "flags": e["flags"],
                        }
                        for e in live[:100]
                    ],
                }
            except ControllerReadUnavailable as exc:
                checks["native"] = result("FAILED", str(exc))
            finally:
                native.close()
            checks.update(native.metadata["stages"])
            if "model" in native.metadata:
                checks["model"] = result(
                    "VERIFIED", "DEVICE_RESPONSE", value=native.metadata["model"]
                )
                checks["firmware"] = result(
                    "VERIFIED",
                    "DEVICE_RAW_RESPONSE",
                    versionRaw=native.metadata["firmwareRaw"],
                    buildDateRaw=native.metadata["buildDateRaw"],
                    packedVersion=native.metadata.get("firmwarePacked"),
                    build=native.metadata.get("firmwareBuild"),
                )
            if "deviceTime" in native.metadata:
                checks["deviceTime"] = result(
                    "VERIFIED",
                    "DEVICE_RESPONSE",
                    value=native.metadata["deviceTime"],
                    clockSkewSeconds=native.metadata["clockSkewSeconds"],
                )
            if "capabilities" in native.metadata:
                checks["accessCapabilities"] = result(
                    "VERIFIED",
                    "DEVICE_RESPONSE",
                    values=native.metadata["capabilities"],
                )
    return {
        "controllerMode": settings.controller_mode,
        "runtime": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "libc": " ".join(platform.libc_ver()),
        },
        "deviceWritesAllowed": False,
        "integrationComplete": False,
        "checks": checks,
        **({"sdkVersionRaw": native.metadata.get("sdkVersionRaw")} if native else {}),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tcp-only",
        action="store_true",
        help="Network preflight only; does not prove login.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Historical event sample bound (1..100); partial samples never move checkpoints.",
    )
    parser.add_argument(
        "--listen-seconds",
        type=int,
        default=0,
        help="Listen for ordinary swipes (0..60 seconds); never sends door commands.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.sample_limit <= 100:
        parser.error("sample limit must be 1..100")
    if not 0 <= args.listen_seconds <= 60:
        parser.error("listen seconds must be 0..60")
    try:
        settings = ControllerSettings()
        report = diagnose(
            settings, args.tcp_only, args.sample_limit, args.listen_seconds
        )
    except (ValidationError, OSError, ValueError):
        # Pydantic's detailed errors may contain the original secret input.
        report = {
            "integrationComplete": False,
            "checks": {
                "configuration": result("FAILED", "INVALID_LOCAL_CONFIGURATION")
            },
        }
    print(json.dumps(report, indent=2))
    checks = report["checks"]
    if args.tcp_only:
        return 0 if checks.get("tcp", {}).get("status") == "VERIFIED" else 2
    return 0 if checks.get("login", {}).get("status") == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
