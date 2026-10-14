"""GET-only comparison of compiled ABI, raw validity bytes and legacy/V50 responses."""
import argparse
import hashlib
import json
import os
import subprocess
from tempfile import TemporaryDirectory
from .config import Settings, ROOT
from .hikvision_card_diagnostic import save, brief_card, WriteBlocked, ROOT_EVIDENCE
from .hikvision_binary import prebuilt


def inspect(settings, number):
    if not number.isdecimal() or not 4 <= len(number) <= 32:
        raise WriteBlocked("INVALID_CARD_NUMBER")
    root = ROOT_EVIDENCE.parent / "validity"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    sdk = settings.hikvision_sdk_dir
    header = sdk / "incEn/HCNetSDK.h"
    values = [settings.hikvision_host, str(settings.hikvision_port),
              settings.hikvision_username.get_secret_value(), settings.hikvision_password.get_secret_value(),
              str(sdk / "lib"), number]
    if not values[2] or not values[3] or any(any(c in v for c in "\r\n\0") for v in values):
        raise WriteBlocked("RUN_CONFIGURE_HIKVISION_PRIVATELY")
    with TemporaryDirectory(prefix="probe-", dir=root) as directory:
        installed = prebuilt(settings, "hikvision_validity_probe.cpp")
        binary = installed or (root / directory / "validity-probe")
        if not installed:
            subprocess.run(["g++", "-std=c++17", "-O2", "-pthread", "-I", str(header.parent),
                        str(ROOT / "backend/native/hikvision_validity_probe.cpp"), "-L", str(sdk / "lib"),
                        "-Wl,-rpath-link," + str(sdk / "lib"), "-lhcnetsdk", "-o", str(binary)],
                           check=True, capture_output=True, timeout=30)
            binary.chmod(0o700)
        completed = subprocess.run([str(binary)], input="\n".join(values) + "\n", text=True,
            capture_output=True, timeout=40, env={"PATH": os.defpath, "LANG": "C",
            "LD_LIBRARY_PATH": str(sdk / "lib") + ":" + str(sdk / "lib/HCNetSDKCom")})
    rows = [json.loads(line[4:]) for line in completed.stdout.splitlines() if line.startswith("IFT ")]
    report = {"number": number, "controllerWrites": 0, "rows": rows,
              "headerSha256": hashlib.sha256(header.read_bytes()).hexdigest(),
              "librarySha256": hashlib.sha256((sdk / "lib/libhcnetsdk.so").read_bytes()).hexdigest()}
    report["artifact"] = save("validity-abi-" + number, report)
    abi = next((r for r in rows if r.get("kind") == "abi"), {})
    card = next((r["data"] for r in rows if r.get("kind") == "permissionRow"), None)
    raw = next((r for r in rows if r.get("kind") == "validityBytes"), None)
    legacy = next((r for r in rows if r.get("kind") == "legacyValidity"), None)
    stages = [r for r in rows if "stage" in r]
    lifecycle = len(stages) == 4 and all(r.get("ok") for r in stages) and completed.returncode == 0
    coherent = bool(card and raw and raw["nativeSize"] == raw["declaredSize"] == raw["callbackLength"]
                    and raw["validityOffset"] == abi.get("v50ValidityOffset")
                    and len(bytes.fromhex(raw["hex"])) == abi.get("validitySize"))
    if coherent:
        data = bytes.fromhex(raw["hex"])
        coherent = all(data[abi["offsets"][k]] == card[k] for k in ["beginFlag", "endFlag"])
        for field, key in [("start", "start"), ("expiry", "expiry")]:
            offset = abi["offsets"][key]
            decoded = [int.from_bytes(data[offset:offset+2], "little"), *data[offset+2:offset+7]]
            coherent = coherent and decoded == card[field]
    return {"artifact": report["artifact"], "abi": abi, "stages": stages, "lifecycle": lifecycle,
            "device": next((r for r in rows if r.get("kind") == "device"), None),
            "record": brief_card(card), "rawV50": raw, "legacy": legacy,
            "endResults": [r for r in rows if r.get("kind") == "permissionEnd"],
            "rawMatchesParsed": coherent, "legacyMatchesV50Validity": bool(raw and legacy and raw["hex"] == legacy["validityHex"])}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number")
    args = parser.parse_args()
    result = inspect(Settings(), args.number)
    print(json.dumps(result, indent=2))
    if not result["lifecycle"] or not result["rawMatchesParsed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
