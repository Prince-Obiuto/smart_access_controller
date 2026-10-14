"""Verify locally built wrappers; vendor libraries stay in the private SDK mount."""

import hashlib
import json
from .config import ROOT
from .hikvision_writes import WriteBlocked


def prebuilt(settings, source):
    directory = settings.hikvision_bridge_path.parent
    binary = directory / (source.removesuffix(".cpp") + ".bin")
    if not binary.exists():
        return None
    try:
        evidence = json.loads(binary.with_suffix(".json").read_text())
        expected = {
            "headerSha256": hashlib.sha256(
                (settings.hikvision_sdk_dir / "incEn/HCNetSDK.h").read_bytes()
            ).hexdigest(),
            "librarySha256": hashlib.sha256(
                (settings.hikvision_sdk_dir / "lib/libhcnetsdk.so").read_bytes()
            ).hexdigest(),
            "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "sources": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "backend/native").iterdir()
                if p.suffix in {".cpp", ".h"}
            },
        }
        if evidence != expected:
            raise ValueError("Build evidence mismatch")
    except (OSError, ValueError, TypeError):
        raise WriteBlocked("NATIVE_BUILD_EVIDENCE_MISMATCH") from None
    return binary
