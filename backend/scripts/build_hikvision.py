"""Compile only our bridge against the owner's local official SDK; no redistribution."""

import hashlib
import json
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ControllerSettings, ROOT


def build(container=False):
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
        str(output),
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
            "-L/sdk/lib -Wl,-rpath-link,/sdk/lib -lhcnetsdk -o /out/ift-hikvision",
        ]
    subprocess.run(command, check=True, cwd=ROOT)
    manifest = {
        "headerSha256": hashlib.sha256(header.read_bytes()).hexdigest(),
        "librarySha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "sourceSha256": hashlib.sha256(
            (ROOT / "backend/native/hikvision_readonly.cpp").read_bytes()
        ).hexdigest(),
    }
    (output.parent / "build.json").write_text(json.dumps(manifest, indent=2))
    print(
        "Read-only bridge compiled against local SDK headers. No device connection or mutation performed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", action="store_true")
    build(parser.parse_args().container)
