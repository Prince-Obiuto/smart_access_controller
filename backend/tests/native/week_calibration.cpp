// Offline SDK stubs: never link libhcnetsdk or contact a controller.
#include "hikvision_week_calibration.h"
#include <cassert>
using namespace ift_calibration;
static NET_DVR_WEEK_PLAN_CFG device{}, sent{};
static unsigned writes = 0, reads = 0;
static bool get_ok = true, set_ok = true, truncate = false, mismatch = false;
BOOL __stdcall NET_DVR_GetDVRConfig(LONG, DWORD command, LONG slot, LPVOID data,
        DWORD size, LPDWORD returned) {
    assert(command == 2126 && slot == 4 && size == sizeof(device)); ++reads;
    if (!get_ok) return FALSE;
    std::memcpy(data, &device, size); *returned = truncate ? size - 1 : size; return TRUE;
}
BOOL __stdcall NET_DVR_SetDVRConfig(LONG, DWORD command, LONG slot, LPVOID data, DWORD size) {
    assert(command == 2127 && slot == 4 && size == sizeof(device)); ++writes;
    std::memcpy(&sent, data, size);
    if (set_ok) device = sent;
    if (mismatch) device.byEnable ^= 1;
    return set_ok ? TRUE : FALSE;
}
DWORD __stdcall NET_DVR_GetLastError() { return 11; }
int main() {
    NET_DVR_WEEK_PLAN_CFG baseline{}; baseline.dwSize = sizeof(baseline);
    NET_DVR_WEEK_PLAN_CFG probe{}; DWORD error = 0;
    assert(proposal(baseline, probe));
    assert(probe.byEnable == 1 && baseline.byEnable == 0 && reserved_zero(probe));
    for (unsigned d = 0; d < MAX_DAYS; ++d) {
        assert(probe.struPlanCfg[d][0].byEnable == 1);
        assert(probe.struPlanCfg[d][0].struTimeSegment.struBeginTime.byMinute == 1 + 2*d);
        assert(probe.struPlanCfg[d][0].struTimeSegment.struEndTime.byMinute == 2 + 2*d);
        for (unsigned s = 1; s < MAX_TIMESEGMENT_V30; ++s)
            assert(!std::memcmp(&probe.struPlanCfg[d][s], &baseline.struPlanCfg[d][s], sizeof(NET_DVR_SINGLE_PLAN_SEGMENT)));
    }
    device = baseline;
    assert(transfer(0, baseline, baseline, false, false, error) == Result::blocked);
    assert(writes == 0 && reads == 0);
    auto nonzero = baseline; nonzero.byRes2[0] = 1;
    assert(!proposal(nonzero, sent));
    nonzero = baseline; nonzero.struPlanCfg[0][0].struTimeSegment.struEndTime.byRes = 1;
    assert(!proposal(nonzero, sent));
    assert(transfer(0, baseline, probe, false, true, error) == Result::blocked);
    device.byEnable = 1;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::conflict && writes == 0);
    device = baseline; truncate = true;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::get_failed && writes == 0);
    truncate = false; get_ok = false;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::get_failed && writes == 0);
    get_ok = true;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::verified && writes == 1);
    assert(!std::memcmp(&device, &probe, sizeof(device)));
    assert(transfer(0, baseline, probe, true, true, error) == Result::verified && writes == 2);
    assert(sent.byEnable == 0 && !std::memcmp(&device, &baseline, sizeof(device)));
    set_ok = false;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::set_uncertain);
    assert(writes == 3 && error == 11); // one SET, no automatic repeat
    set_ok = true; mismatch = true; device = baseline;
    assert(transfer(0, baseline, baseline, false, true, error) == Result::mismatch && writes == 4);
}
