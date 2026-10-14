"""Compile only our bridge against the owner's local official SDK; no redistribution."""

import hashlib
import json
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ControllerSettings, ROOT


def build(container=False, permissions=False):
    settings = ControllerSettings()
    sdk = settings.hikvision_sdk_dir
    header = sdk / "incEn/HCNetSDK.h"
    library = sdk / "lib/libhcnetsdk.so"
    if not header.is_file() or not library.is_file():
        raise SystemExit(
            "Set HIKVISION_SDK_DIR to the extracted official SDK root containing incEn/ and lib/."
        )
    output = ROOT / (
        "backend/var/hikvision-container/ift-hikvision"
        if container
        else "backend/var/hikvision/ift-hikvision"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "g++",
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-pthread",
        "-I",
        str(header.parent),
        str(ROOT / "backend/native/hikvision_readonly.cpp"),
        "-L",
        str(library.parent),
        "-Wl,-rpath-link," + str(library.parent),
        "-lhcnetsdk",
        "-o",
        str(output.with_suffix(".new")),
    ]
    if container:
        # Same Debian runtime as the API, not a host binary requiring newer
        # glibc/libstdc++. Compiler exists only in this disposable container.
        command = [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--user",
            "root",
            "--volume",
            str(sdk) + ":/sdk:ro",
            "--volume",
            str(ROOT / "backend/native") + ":/src:ro",
            "--volume",
            str(output.parent) + ":/out",
            "backend",
            "sh",
            "-c",
            "apt-get update >/dev/null && apt-get install -y --no-install-recommends g++ >/dev/null && "
            "g++ -std=c++17 -O2 -Wall -Wextra -pthread -I/sdk/incEn /src/hikvision_readonly.cpp "
            "-L/sdk/lib -Wl,-rpath-link,/sdk/lib -lhcnetsdk -o /out/ift-hikvision.new",
        ]
    subprocess.run(command, check=True, cwd=ROOT, timeout=300)
    os.replace(output.with_suffix(".new"), output)
    manifest = {
        "headerSha256": hashlib.sha256(header.read_bytes()).hexdigest(),
        "librarySha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "sourceSha256": hashlib.sha256(
            (ROOT / "backend/native/hikvision_readonly.cpp").read_bytes()
        ).hexdigest(),
        "permissionsSourceSha256": hashlib.sha256(
            (ROOT / "backend/native/hikvision_permissions.h").read_bytes()
        ).hexdigest(),
    }
    (output.parent / "build.json").write_text(json.dumps(manifest, indent=2))
    if permissions:
        sources = ["hikvision_existing_template.cpp", "hikvision_validity_probe.cpp", "hikvision_timed_unlock.cpp"]
        if container:
            script = "apt-get update >/dev/null && apt-get install -y --no-install-recommends g++ >/dev/null"
            for source in sources:
                script += (" && g++ -std=c++17 -O2 -pthread -I/sdk/incEn /src/" + source
                           + " -L/sdk/lib -Wl,-rpath-link,/sdk/lib -lhcnetsdk -o /out/"
                           + source.removesuffix(".cpp") + ".bin")
            subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "--user", "root",
                "--volume", str(sdk) + ":/sdk:ro", "--volume", str(ROOT / "backend/native") + ":/src:ro",
                "--volume", str(output.parent) + ":/out", "backend", "sh", "-c", script], check=True, timeout=300)
        else:
            for source in sources:
                subprocess.run(["g++", "-std=c++17", "-O2", "-pthread", "-I", str(header.parent),
                    str(ROOT / "backend/native" / source), "-L", str(library.parent),
                    "-Wl,-rpath-link," + str(library.parent), "-lhcnetsdk", "-o",
                    str(output.parent / (source.removesuffix(".cpp") + ".bin"))], check=True, timeout=30)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (ROOT / "backend/native").iterdir() if p.suffix in {".cpp", ".h"}}
        for source in sources:
            binary = output.parent / (source.removesuffix(".cpp") + ".bin")
            binary.with_suffix(".json").write_text(json.dumps({"headerSha256": manifest["headerSha256"],
                "librarySha256": manifest["librarySha256"], "sources": hashes,
                "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2))
    print(
        "Bridge compiled against local SDK headers. No device connection or mutation performed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", action="store_true")
    parser.add_argument("--permissions", action="store_true", help="Also build the reviewed card/validity/unlock wrappers; no controller connection")
    args = parser.parse_args()
    build(args.container, args.permissions)
