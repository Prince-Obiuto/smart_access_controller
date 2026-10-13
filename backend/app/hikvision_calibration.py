"""Prepare isolated WEEK 4 evidence; no activation, database mutation or SDK SET.

The ordinary controlled-write gates remain unchanged. Reference completeness
may be proved independently of numeric template capacity, but not by a readable
prefix, an operator's 'unused' assertion or absence of active card references.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .config import Settings, ROOT


def gates(report):
    stages = report.get("stages", [])
    baseline = report.get("baseline", {})
    templates = report.get("templates", [])
    cards = report.get("cards", [])
    blockers = []
    if len(stages) != 4 or not all(s.get("ok") for s in stages):
        blockers.append("SDK_LIFECYCLE_INCOMPLETE")
    if not (baseline.get("ok") and baseline.get("disabledZero") and
            baseline.get("reservedZero") and report.get("stable")):
        blockers.append("EXACT_DISABLED_BASELINE_REQUIRED")
    raw = baseline.get("hex", "")
    if (len(raw) != baseline.get("size", 0) * 2 or
            not re.fullmatch(r"[0-9a-f]+", raw)):
        blockers.append("COMPLETE_NATIVE_PREIMAGE_REQUIRED")
    if ({t.get("id") for t in templates} != set(range(1, 129)) or
            len(templates) != 128 or not all(t.get("ok") for t in templates)):
        blockers.append("READABLE_PREFIX_INCOMPLETE")
    if any(t.get("week") == 4 for t in templates if t.get("ok")):
        blockers.append("EXISTING_TEMPLATE_REFERENCE_PROTECTED")
    if report.get("cardEnd", {}).get("code") != "OK":
        blockers.append("CARD_INVENTORY_INCOMPLETE")
    by_id = {t["id"]: t for t in templates if t.get("ok")}
    used = {n for c in cards for plans in c.get("plans", []) for n in plans if n}
    if any(n not in by_id or by_id[n].get("week") == 4 for n in used):
        blockers.append("LIVE_PERMISSION_REFERENCE_UNSAFE")
    # No supported complete reverse-reference source has been established on
    # this firmware. Deliberately not satisfiable by injecting JSON flags.
    blockers += ["ALL_TEMPLATE_REFERENCES_UNVERIFIED",
                 "INDEPENDENT_DEVICE_WEEKDAY_VIEW_UNVERIFIED",
                 "SCOPED_OWNER_AUTHORIZATION_REQUIRED"]
    return {"operation": "CARD_RIGHT_WEEK:4", "templateCapacity": None,
            "totalCapacityNeededForThisOperation": False,
            "unusedVerified": False, "activeCardTemplateIds": sorted(used),
            "blockers": blockers, "executable": False}


def prepare(settings, output):
    if settings.controller_mode != "HIKVISION_READ_ONLY" or settings.controller_writes_available:
        raise SystemExit("READ_ONLY_REQUIRED")
    if output.exists():
        raise SystemExit("Refusing to replace an evidence directory")
    output.mkdir(mode=0o700, parents=True)
    sdk = settings.hikvision_sdk_dir
    source = ROOT / "backend/native/hikvision_calibration_read.cpp"
    header = sdk / "incEn/HCNetSDK.h"
    binary = output / "read-calibration"
    subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-pthread",
                    "-I", str(header.parent), str(source), "-L", str(sdk / "lib"),
                    "-Wl,-rpath-link," + str(sdk / "lib"), "-lhcnetsdk", "-o", str(binary)],
                   check=True, timeout=40, capture_output=True)
    binary.chmod(0o700)
    values = [settings.hikvision_host, str(settings.hikvision_port),
              settings.hikvision_username.get_secret_value(),
              settings.hikvision_password.get_secret_value(), str(sdk / "lib")]
    if not values[2] or not values[3]:
        raise SystemExit("Run backend/scripts/configure_hikvision.py privately")
    if any(any(c in v for c in "\r\n\0") for v in values):
        raise SystemExit("INVALID_PRIVATE_CONFIGURATION")
    env = {"PATH": os.defpath, "LANG": "C", "LD_LIBRARY_PATH":
           str(sdk / "lib") + ":" + str(sdk / "lib/HCNetSDKCom")}
    # Bounded whole-process timeout; no native SET is dispatched by this binary.
    done = subprocess.run([str(binary)], input="\n".join(values) + "\n", text=True,
                          capture_output=True, timeout=90, env=env)
    rows = [json.loads(line[4:]) for line in done.stdout.splitlines() if line.startswith("IFT ")]
    def one(kind):
        found = [r for r in rows if r.get("kind") == kind]
        return found[0] if len(found) == 1 else {}
    report = {"at": datetime.now(timezone.utc).isoformat(), "mode": settings.controller_mode,
              "device": one("device"), "stages": [r for r in rows if "stage" in r],
              "baseline": one("weekBaseline"), "fields": one("weekFields"), "stable": one("weekStable").get("ok", False),
              "proposed": one("proposedWeek"), "templates": [r for r in rows if r.get("kind") == "template"],
              "cards": [r["data"] for r in rows if r.get("kind") == "permissionRow"],
              "cardEnd": one("permissionEnd"), "processReturnCode": done.returncode,
              "controllerWrites": 0, "capabilityQueries": 0,
              "referenceCoverage": "READABLE_TEMPLATE_PREFIX_ONLY_1_128",
              "headerSha256": hashlib.sha256(header.read_bytes()).hexdigest(),
              "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "helperSha256": hashlib.sha256((source.parent / "hikvision_week_calibration.h").read_bytes()).hexdigest()}
    if done.returncode:
        report["stable"] = False
    report["gates"] = gates(report)
    for name, value in [("evidence.json", report)]:
        with (output / name).open("x") as handle:
            json.dump(value, handle, indent=2)
    for name, value in [("week-4-baseline.bin", report["baseline"]),
                        ("week-4-proposed.bin", report["proposed"])]:
        if value.get("hex"):
            (output / name).write_bytes(bytes.fromhex(value["hex"]))
    summary = {"at": report["at"], "baselineSize": report["baseline"].get("size"),
               "baselineSha256": hashlib.sha256(bytes.fromhex(report["baseline"].get("hex", ""))).hexdigest(),
               "baselineStable": report["stable"], "cards": len(report["cards"]),
               "templatesRead": len(report["templates"]), "gates": report["gates"],
               "controllerWrites": 0, "capabilityQueries": 0, "artifact": str(output)}
    print(json.dumps(summary, indent=2))
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(Settings(), args.output.resolve())


if __name__ == "__main__":
    main()
