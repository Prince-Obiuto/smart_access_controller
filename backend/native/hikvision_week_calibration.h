// Owner-installed HCNetSDK.h supplies every type, size and calling convention.
// This helper is NOT wired into the API, worker or permission command channel.
#pragma once
#include "HCNetSDK.h"
#include <algorithm>
#include <cstring>
#include <string>

namespace ift_calibration {
constexpr LONG week_id = 4;

template <typename T> inline bool zero(const T &bytes) {
    return std::all_of(std::begin(bytes), std::end(bytes), [](BYTE b) { return b == 0; });
}
inline bool reserved_zero(const NET_DVR_WEEK_PLAN_CFG &w) {
    if (!zero(w.byRes1) || !zero(w.byRes2)) return false;
    for (const auto &day : w.struPlanCfg) for (const auto &s : day)
        if (!zero(s.byRes) || s.struTimeSegment.struBeginTime.byRes ||
            s.struTimeSegment.struEndTime.byRes) return false;
    return true;
}
inline bool disabled_baseline(const NET_DVR_WEEK_PLAN_CFG &w) {
    if (w.dwSize != sizeof(w) || w.byEnable != 0 || !reserved_zero(w)) return false;
    for (const auto &day : w.struPlanCfg) for (const auto &s : day) {
        const NET_DVR_SINGLE_PLAN_SEGMENT empty{};
        if (std::memcmp(&s, &empty, sizeof(s))) return false;
    }
    return true;
}
inline std::string hex(const void *data, size_t size) {
    static const char *digits = "0123456789abcdef";
    std::string out; out.reserve(size * 2);
    const auto *bytes = static_cast<const unsigned char *>(data);
    for (size_t i = 0; i < size; ++i) { out += digits[bytes[i] >> 4]; out += digits[bytes[i] & 15]; }
    return out;
}
inline bool read_week(LONG user, NET_DVR_WEEK_PLAN_CFG &week, DWORD &error) {
    week = {}; week.dwSize = sizeof(week); DWORD returned = 0;
    const BOOL ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,
        week_id, &week, sizeof(week), &returned);
    error = ok ? 0 : NET_DVR_GetLastError();
    return ok && returned == sizeof(week) && week.dwSize == sizeof(week);
}
// Index fingerprints, not weekday names. An independent device-read iVMS
// labelled view must establish all seven meanings after an authorized write.
inline bool proposal(const NET_DVR_WEEK_PLAN_CFG &baseline, NET_DVR_WEEK_PLAN_CFG &probe) {
    if (!disabled_baseline(baseline)) return false;
    probe = baseline; probe.byEnable = 1;
    for (unsigned index = 0; index < MAX_DAYS; ++index) {
        auto &s = probe.struPlanCfg[index][0]; s.byEnable = 1;
        s.struTimeSegment.struBeginTime.byHour = 8;
        s.struTimeSegment.struBeginTime.byMinute = 1 + 2 * index;
        s.struTimeSegment.struEndTime.byHour = 8;
        s.struTimeSegment.struEndTime.byMinute = 2 + 2 * index;
    }
    return true;
}
enum class Result { blocked, conflict, get_failed, set_uncertain, mismatch, verified };
// The trusted caller must independently verify reference coverage, device and
// header identity, fresh hashes and scoped owner authorization. Default denies.
// No retry of SET: after an uncertain result, GET/reconcile before any action.
inline Result transfer(LONG user, const NET_DVR_WEEK_PLAN_CFG &baseline,
        const NET_DVR_WEEK_PLAN_CFG &expected_current, bool restore,
        bool isolated_scope_verified, DWORD &error) {
    error = 0; NET_DVR_WEEK_PLAN_CFG probe{};
    if (!isolated_scope_verified || !proposal(baseline, probe)) return Result::blocked;
    if (std::memcmp(&expected_current, restore ? &probe : &baseline, sizeof(baseline)))
        return Result::blocked;
    NET_DVR_WEEK_PLAN_CFG current{};
    if (!read_week(user, current, error)) return Result::get_failed;
    if (std::memcmp(&current, &expected_current, sizeof(current))) return Result::conflict;
    // Exact struct preimage for restoration includes byEnable=0 and all reserved
    // bytes. Nonzero reserved preimages are blocked; vendor definitions require 0.
    auto payload = restore ? baseline : probe;
    const BOOL ok = NET_DVR_SetDVRConfig(user, NET_DVR_SET_CARD_RIGHT_WEEK_PLAN,
        week_id, &payload, sizeof(payload));
    const DWORD set_error = ok ? 0 : NET_DVR_GetLastError();
    NET_DVR_WEEK_PLAN_CFG after{};
    const bool got = read_week(user, after, error);
    if (!ok) { error = set_error; return Result::set_uncertain; }
    if (!got) return Result::get_failed;
    return std::memcmp(&after, &payload, sizeof(after)) ? Result::mismatch : Result::verified;
}
} // namespace ift_calibration
