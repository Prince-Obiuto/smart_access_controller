"""No database fixtures, live SDK calls, controller writes or owner credentials."""
from copy import deepcopy
from pathlib import Path
import subprocess
import pytest

from app.hikvision_calibration import gates, prepare
from app.config import ROOT, Settings


def observed():
    return {"stages": [{"ok": True}] * 4,
            "baseline": {"ok": True, "disabledZero": True, "reservedZero": True,
                         "size": 4, "hex": "00000000"}, "stable": True,
            "templates": [{"id": n, "ok": True, "week": 1 if n == 1 else 0}
                          for n in range(1, 129)],
            "cards": [{"card": "1050853895", "plans": [[1, 0, 0, 0]]}],
            "cardEnd": {"code": "OK"}}


def test_readable_prefix_and_no_active_references_do_not_prove_unused():
    result = gates(observed())
    assert result["templateCapacity"] is None
    assert result["totalCapacityNeededForThisOperation"] is False
    assert result["activeCardTemplateIds"] == [1]
    assert result["unusedVerified"] is False and result["executable"] is False
    assert "ALL_TEMPLATE_REFERENCES_UNVERIFIED" in result["blockers"]


@pytest.mark.parametrize("change,blocker", [
    ("partial", "READABLE_PREFIX_INCOMPLETE"),
    ("reference", "EXISTING_TEMPLATE_REFERENCE_PROTECTED"),
    ("unknownCardTemplate", "LIVE_PERMISSION_REFERENCE_UNSAFE"),
    ("gap", "CARD_INVENTORY_INCOMPLETE"),
    ("reserved", "EXACT_DISABLED_BASELINE_REQUIRED"),
    ("short", "COMPLETE_NATIVE_PREIMAGE_REQUIRED"),
    ("changed", "EXACT_DISABLED_BASELINE_REQUIRED"),
])
def test_evidence_failures_block(change, blocker):
    report = deepcopy(observed())
    if change == "partial": report["templates"].pop()
    if change == "reference": report["templates"][2]["week"] = 4
    if change == "unknownCardTemplate": report["cards"][0]["plans"] = [[200]]
    if change == "gap": report["cardEnd"]["code"] = "CALLBACK_GAP"
    if change == "reserved": report["baseline"]["reservedZero"] = False
    if change == "short": report["baseline"]["hex"] = "00"
    if change == "changed": report["stable"] = False
    assert blocker in gates(report)["blockers"]


def test_cannot_inject_capacity_or_reference_approval_to_bypass_gate():
    report = observed()
    report.update(templateCapacity=128, referenceCoverage="COMPLETE", ownerApproved=True)
    assert gates(report)["executable"] is False


def test_prepare_rejects_write_mode_before_build_or_connection(tmp_path):
    settings = Settings(controller_mode="HIKVISION_CONTROLLED")
    with pytest.raises(SystemExit, match="READ_ONLY_REQUIRED"):
        prepare(settings, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_native_exact_disabled_restore_and_failure_handling(tmp_path):
    header = Settings().hikvision_sdk_dir / "incEn/HCNetSDK.h"
    if not header.is_file():
        pytest.skip("Owner-installed SDK required for matching-header offline ABI check")
    executable = tmp_path / "offline-native-test"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(header.parent), "-I", str(ROOT / "backend/native"),
                    str(Path(__file__).parent / "native/week_calibration.cpp"),
                    "-o", str(executable)], check=True, timeout=30, capture_output=True)
    # No SDK library linked: Get/Set are assertions against in-memory test state.
    subprocess.run([str(executable)], check=True, timeout=5)
