// Read-only calibration preflight: WEEK 4, templates 1..128 (known readable
// prefix only), complete card enumeration. No capability or write dispatch.
#include "HCNetSDK.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/resource.h>
#include <thread>
#include <vector>
using Clock = std::chrono::steady_clock;
static volatile std::sig_atomic_t stopping = 0;
static std::string quote(const std::string &s) {
    std::string out = "\"";
    const char *hex = "0123456789abcdef";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c < 32 || c >= 127) {
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += c;
    }
    return out + '"';
}
static void emit(const std::string &s) { std::cout << "IFT " << s << std::endl; }
template <typename T> static std::string text(T *p, size_t n) {
    return std::string(reinterpret_cast<const char *>(p), strnlen(reinterpret_cast<const char *>(p), n));
}
#include "hikvision_permissions.h"
#include "hikvision_week_calibration.h"
static bool stage(const char *name, BOOL ok) {
    DWORD error = ok ? 0 : NET_DVR_GetLastError();
    emit("{\"stage\":" + quote(name) + ",\"ok\":" + (ok ? "true" : "false") +
         ",\"error\":" + std::to_string(error) + "}");
    return ok;
}
int main() {
    rlimit core{0,0}; setrlimit(RLIMIT_CORE, &core);
    std::string host, port, username, password, lib;
    for (auto *v : {&host, &port, &username, &password, &lib})
        if (!std::getline(std::cin, *v)) return 2;
    NET_DVR_LOCAL_SDK_PATH path{}; NET_DVR_USER_LOGIN_INFO login{};
    if (host.size() >= sizeof(login.sDeviceAddress) ||
        username.size() >= sizeof(login.sUserName) || password.size() >= sizeof(login.sPassword) ||
        lib.size() >= sizeof(path.sPath)) return 2;
    std::memcpy(path.sPath, lib.data(), lib.size());
    std::string crypto = lib + "/libcrypto.so.1.1", ssl = lib + "/libssl.so.1.1";
    BOOL initialized = NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, &path) &&
        NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, crypto.data()) &&
        NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, ssl.data()) && NET_DVR_Init();
    if (!stage("initialize", initialized)) { stage("cleanup", NET_DVR_Cleanup()); return 2; }
    NET_DVR_SetConnectTime(3000, 1); NET_DVR_SetRecvTimeOut(3000); NET_DVR_SetReconnect(30000, FALSE);
    std::memcpy(login.sDeviceAddress, host.data(), host.size());
    std::memcpy(login.sUserName, username.data(), username.size());
    std::memcpy(login.sPassword, password.data(), password.size());
    login.wPort = static_cast<WORD>(std::stoul(port)); login.bUseAsynLogin = FALSE; login.byLoginMode = 0;
    NET_DVR_DEVICEINFO_V40 info{}; LONG user = NET_DVR_Login_V40(&login, &info);
    std::fill(password.begin(), password.end(), '\0'); std::memset(login.sPassword, 0, sizeof(login.sPassword));
    if (!stage("login", user >= 0)) { stage("cleanup", NET_DVR_Cleanup()); return 2; }
    emit("{\"kind\":\"device\",\"serial\":" + quote(text(info.struDeviceV30.sSerialNumber, SERIALNO_LEN)) + "}");
    NET_DVR_WEEK_PLAN_CFG before{}, again{}, probe{}; DWORD error = 0;
    bool got = ift_calibration::read_week(user, before, error);
    emit("{\"kind\":\"weekBaseline\",\"id\":4,\"ok\":" + std::string(got ? "true" : "false") +
        ",\"error\":" + std::to_string(error) + ",\"size\":" + std::to_string(sizeof(before)) +
        ",\"hex\":" + quote(ift_calibration::hex(&before, sizeof(before))) +
        ",\"reservedZero\":" + (ift_calibration::reserved_zero(before) ? "true" : "false") +
        ",\"disabledZero\":" + (ift_calibration::disabled_baseline(before) ? "true" : "false") + "}");
    std::string days = "[";
    for (unsigned d = 0; d < MAX_DAYS; ++d) {
        if (d) days += ','; days += '[';
        for (unsigned s = 0; s < MAX_TIMESEGMENT_V30; ++s) {
            if (s) days += ','; days += segment_json(before.struPlanCfg[d][s]);
        }
        days += ']';
    }
    emit("{\"kind\":\"weekFields\",\"enabled\":" + std::to_string(before.byEnable) + ",\"days\":" + days + "]}");
    bool repeated = ift_calibration::read_week(user, again, error);
    emit("{\"kind\":\"weekStable\",\"ok\":" + std::string(got && repeated && !std::memcmp(&before, &again, sizeof(before)) ? "true" : "false") +
        ",\"error\":" + std::to_string(error) + "}");
    if (got && ift_calibration::proposal(before, probe))
        emit("{\"kind\":\"proposedWeek\",\"hex\":" + quote(ift_calibration::hex(&probe, sizeof(probe))) + "}");
    // Never interpret this readable prefix as the total template namespace.
    for (LONG id = 1; id <= 128; ++id) {
        NET_DVR_PLAN_TEMPLATE record{}; record.dwSize = sizeof(record); DWORD returned = 0;
        BOOL ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_PLAN_TEMPLATE,
            id, &record, sizeof(record), &returned);
        DWORD e = ok ? 0 : NET_DVR_GetLastError();
        bool complete = ok && returned == sizeof(record) && record.dwSize == sizeof(record);
        emit("{\"kind\":\"template\",\"id\":" + std::to_string(id) +
            ",\"ok\":" + (complete ? "true" : "false") + ",\"returned\":" + std::to_string(returned) +
            ",\"size\":" + std::to_string(sizeof(record)) + ",\"error\":" + std::to_string(e) +
            ",\"enabled\":" + std::to_string(record.byEnable) + ",\"week\":" + std::to_string(record.dwWeekPlanNo) +
            ",\"hex\":" + quote(ift_calibration::hex(&record, sizeof(record))) + "}");
        if (!complete) break; // failure is incomplete evidence, never "unused"
    }
    // nullptr selects GET. Completion/callback-gap status must be checked.
    card_request(user, 1, "ALL", nullptr, 10000);
    bool logout = stage("logout", NET_DVR_Logout(user));
    bool cleanup = stage("cleanup", NET_DVR_Cleanup());
    return logout && cleanup ? 0 : 2;
}
