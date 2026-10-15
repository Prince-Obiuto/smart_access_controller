"""Compile against the installed SDK; overridden GET/SET never reach hardware."""

import os
import shutil
import subprocess

import pytest
from app.config import ControllerSettings, ROOT


def test_typed_plan_preparation_restoration_reserved_bytes_and_stale_rejection(
    tmp_path,
):
    sdk = ControllerSettings().hikvision_sdk_dir
    if not (sdk / "incEn/HCNetSDK.h").exists() or not shutil.which("g++"):
        pytest.skip("Owner SDK and compiler required for native ABI regression")
    source = tmp_path / "management_test.cpp"
    source.write_text(r"""
#define main original_bridge_main
#include "hikvision_readonly.cpp"
#undef main
#include <cassert>
static NET_DVR_WEEK_PLAN_CFG saved;
static unsigned sets=0;
extern "C" BOOL NET_DVR_GetDVRConfig(LONG,DWORD command,LONG,LPVOID out,DWORD size,LPDWORD returned) {
    assert(command==NET_DVR_GET_CARD_RIGHT_WEEK_PLAN);assert(size==sizeof(saved));
    std::memcpy(out,&saved,sizeof(saved));*returned=sizeof(saved);return TRUE;
}
extern "C" BOOL NET_DVR_SetDVRConfig(LONG,DWORD command,LONG,LPVOID in,DWORD size) {
    assert(command==NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);assert(size==sizeof(saved));
    std::memcpy(&saved,in,sizeof(saved));++sets;return TRUE;
}
int main() {
    std::memset(&saved,0x5a,sizeof(saved));saved.dwSize=sizeof(saved);saved.byEnable=0;
    const auto original=saved;
    std::string payload;
    for(unsigned i=0;i<56;++i)payload+="1 0 86400 ";
    auto proposed=saved;std::istringstream edits(payload);
    assert(patch_plan(edits,proposed));proposed.byEnable=1;
    assert(std::memcmp(proposed.byRes1,original.byRes1,sizeof(saved.byRes1))==0);
    assert(std::memcmp(proposed.byRes2,original.byRes2,sizeof(saved.byRes2))==0);
    for(auto &day:proposed.struPlanCfg)for(auto &s:day) {
        assert(s.byDoorStatus==0x5a&&s.byVerifyMode==0x5a);
        assert(s.struTimeSegment.struEndTime.byHour==24);
        assert(s.struTimeSegment.struEndTime.byRes==0x5a);
        for(auto n:s.byRes)assert(n==0x5a);
    }
    allowed_plans={4};
    std::istringstream prepare(native_hex(saved)+" 1 "+payload);
    management_plan<NET_DVR_WEEK_PLAN_CFG>(0,1,prepare,"PREPARE",4,NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);
    assert(sets==0&&native_hex(saved)==native_hex(original));
    std::istringstream apply(native_hex(saved)+" "+native_hex(proposed));
    management_plan<NET_DVR_WEEK_PLAN_CFG>(0,2,apply,"APPLY",4,NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);
    assert(sets==1&&native_hex(saved)==native_hex(proposed));
    std::istringstream stale(native_hex(original)+" "+native_hex(proposed));
    management_plan<NET_DVR_WEEK_PLAN_CFG>(0,3,stale,"APPLY",4,NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);
    assert(sets==1);
    std::istringstream restore(native_hex(saved)+" "+native_hex(original));
    management_plan<NET_DVR_WEEK_PLAN_CFG>(0,4,restore,"APPLY",4,NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);
    assert(sets==2&&std::memcmp(&saved,&original,sizeof(saved))==0);
}
""")
    binary = tmp_path / "management-test"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O0",
            "-pthread",
            "-I",
            str(sdk / "incEn"),
            "-I",
            str(ROOT / "backend/native"),
            str(source),
            "-L",
            str(sdk / "lib"),
            "-Wl,-rpath-link," + str(sdk / "lib"),
            "-lhcnetsdk",
            "-o",
            str(binary),
        ],
        check=True,
        timeout=45,
        capture_output=True,
    )
    result = subprocess.run(
        [str(binary)],
        check=True,
        timeout=10,
        capture_output=True,
        env={
            "PATH": os.defpath,
            "LD_LIBRARY_PATH": str(sdk / "lib") + ":" + str(sdk / "lib/HCNetSDKCom"),
        },
    )
    assert b'"REQUIRES_RECONCILIATION"' in result.stdout
