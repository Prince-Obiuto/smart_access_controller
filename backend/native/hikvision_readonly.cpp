// IFT read-only bridge. All Hikvision ABI types/callbacks come from the local
// vendor HCNetSDK.h; no vendor headers or binaries are copied into this repo.
#include "HCNetSDK.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <iostream>
#include <mutex>
#include <poll.h>
#include <sstream>
#include <string>
#include <sys/resource.h>
#include <thread>
#include <unistd.h>

using Clock = std::chrono::steady_clock;
static volatile std::sig_atomic_t stopping = 0;
static void stop_signal(int) { stopping = 1; }
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
static std::string time_json(const NET_DVR_TIME &t) {
    return "[" + std::to_string(t.dwYear) + "," + std::to_string(t.dwMonth) + "," +
        std::to_string(t.dwDay) + "," + std::to_string(t.dwHour) + "," +
        std::to_string(t.dwMinute) + "," + std::to_string(t.dwSecond) + "]";
}
static void status(const std::string &stage, bool ok, DWORD error = 0) {
    const char *state = ok ? "VERIFIED" : error == NET_DVR_NOSUPPORT ? "UNSUPPORTED" : "FAILED";
    emit("{\"kind\":\"status\",\"stage\":" + quote(stage) + ",\"status\":" + quote(state) +
         ",\"error\":" + std::to_string(error) + "}");
}
struct Event {
    DWORD major = 0, minor = 0, serial = 0, door = 0, reader = 0;
    WORD inductive = 0;
    BYTE time_type = 255;
    NET_DVR_TIME at{};
    std::array<BYTE, ACS_CARD_NO_LEN> card{};
};
static std::array<Event, 1024> queue_events;
static size_t queue_head = 0, queue_size = 0;
static std::mutex queue_mutex;
static std::atomic<unsigned> lost{0}, connection_fault{0};
template <typename T> static Event copy_event(const T &a) {
    Event e;
    e.major = a.dwMajor; e.minor = a.dwMinor; e.at = a.struTime;
    e.time_type = a.byTimeType; e.inductive = a.wInductiveEventType;
    e.serial = a.struAcsEventInfo.dwSerialNo; e.door = a.struAcsEventInfo.dwDoorNo;
    e.reader = a.struAcsEventInfo.dwCardReaderNo;
    std::copy(std::begin(a.struAcsEventInfo.byCardNo), std::end(a.struAcsEventInfo.byCardNo), e.card.begin());
    return e;
}
// Callback performs a bounded scalar copy only. No DB, I/O, image pointers,
// string allocation or waiting for the application while on an SDK thread.
static BOOL CALLBACK alarm_callback(LONG command, NET_DVR_ALARMER *, char *buffer, DWORD length, void *) {
    if (command != COMM_ALARM_ACS) return TRUE;
    if (!buffer || length < sizeof(NET_DVR_ACS_ALARM_INFO)) { ++lost; return TRUE; }
    NET_DVR_ACS_ALARM_INFO alarm{};
    std::memcpy(&alarm, buffer, sizeof(alarm));
    std::unique_lock<std::mutex> lock(queue_mutex, std::try_to_lock);
    if (!lock.owns_lock() || queue_size == queue_events.size()) { ++lost; return TRUE; }
    queue_events[(queue_head + queue_size) % queue_events.size()] = copy_event(alarm);
    ++queue_size;
    return TRUE;
}
static void CALLBACK exception_callback(DWORD type, LONG, LONG, void *) {
    if (type == EXCEPTION_EXCHANGE || type == EXCEPTION_ALARM ||
        type == EXCEPTION_LOST_ALARM || type == EXCEPTION_MAX_ALARM_INFO ||
        type == EXCEPTION_RELOGIN_FAILED) connection_fault = type;
}
static std::string outcome(const Event &e) {
    // The package documents nonzero inductive types as taking precedence but
    // supplies no numeric outcome mapping. Retain them without guessing.
    if (e.major != MAJOR_EVENT || e.inductive != 0) return "UNKNOWN";
    if (e.minor == MINOR_LEGAL_CARD_PASS || e.minor == MINOR_CARD_AND_PSW_PASS) return "GRANTED";
    if (e.minor == MINOR_CARD_AND_PSW_FAIL || e.minor == MINOR_CARD_AND_PSW_TIMEOUT ||
        e.minor == MINOR_CARD_AND_PSW_OVER_TIME || e.minor == MINOR_CARD_NO_RIGHT ||
        e.minor == MINOR_CARD_INVALID_PERIOD || e.minor == MINOR_CARD_OUT_OF_DATE ||
        e.minor == MINOR_INVALID_CARD || e.minor == MINOR_ANTI_SNEAK_FAIL) return "DENIED";
    return "UNKNOWN";
}
static void event_json(const Event &e, const std::string &feed, unsigned request = 0) {
    static const char *hex = "0123456789abcdef";
    std::string raw;
    for (BYTE c : e.card) { raw += hex[c >> 4]; raw += hex[c & 15]; }
    emit("{\"kind\":\"event\",\"feed\":" + quote(feed) + ",\"request\":" + std::to_string(request) +
        ",\"major\":" + std::to_string(e.major) + ",\"minor\":" + std::to_string(e.minor) +
        ",\"serial\":" + std::to_string(e.serial) + ",\"door\":" + std::to_string(e.door) +
        ",\"reader\":" + std::to_string(e.reader) + ",\"time\":" + time_json(e.at) +
        ",\"inductive\":" + std::to_string(e.inductive) +
        ",\"timeType\":" + std::to_string(e.time_type) + ",\"cardHex\":" + quote(raw) +
        ",\"outcome\":" + quote(outcome(e)) + "}");
}
static void drain() {
    while (true) {
        Event e;
        {
            std::lock_guard<std::mutex> lock(queue_mutex);
            if (!queue_size) break;
            e = queue_events[queue_head]; queue_head = (queue_head + 1) % queue_events.size(); --queue_size;
        }
        event_json(e, "LIVE");
    }
    if (unsigned n = lost.exchange(0)) {
        emit("{\"kind\":\"fault\",\"code\":\"CALLBACK_GAP\",\"count\":" + std::to_string(n) + "}");
        stopping = 1;
    }
    if (connection_fault.exchange(0)) {
        emit("{\"kind\":\"fault\",\"code\":\"CONNECTION_LOST\"}"); stopping = 1;
    }
}
static NET_DVR_TIME parse_time(std::istringstream &in) {
    NET_DVR_TIME t{};
    in >> t.dwYear >> t.dwMonth >> t.dwDay >> t.dwHour >> t.dwMinute >> t.dwSecond;
    return t;
}
static void history(LONG user, std::istringstream &in, unsigned timeout_ms) {
    unsigned request = 0, limit = 0; in >> request >> limit;
    NET_DVR_ACS_EVENT_COND cond{}; cond.dwSize = sizeof(cond);
    cond.struStartTime = parse_time(in); cond.struEndTime = parse_time(in);
    cond.byTimeType = 0; // Explicit Africa/Lagos wall time supplied by Python.
    if (!in || !limit || limit > 10000) { status("history", false, NET_DVR_PARAMETER_ERROR); return; }
    LONG handle = NET_DVR_StartRemoteConfig(user, NET_DVR_GET_ACS_EVENT, &cond, sizeof(cond), nullptr, nullptr);
    DWORD error = handle < 0 ? NET_DVR_GetLastError() : 0;
    bool complete = false; unsigned count = 0;
    if (handle >= 0) {
        auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
        while (!stopping && Clock::now() < deadline) {
            NET_DVR_ACS_EVENT_CFG cfg{}; cfg.dwSize = sizeof(cfg);
            LONG next = NET_DVR_GetNextRemoteConfig(handle, &cfg, sizeof(cfg));
            if (next == NET_SDK_GET_NEXT_STATUS_SUCCESS) {
                if (count >= limit) break; // Sample/partial read must not advance checkpoint.
                event_json(copy_event(cfg), "HISTORY", request); ++count;
            } else if (next == NET_SDK_GET_NEXT_STATUS_FINISH) { complete = true; break; }
            else if (next != NET_SDK_GET_NETX_STATUS_NEED_WAIT) {
                error = next < 0 ? NET_DVR_GetLastError() : NET_DVR_NETWORK_ERRORDATA; break;
            } else std::this_thread::sleep_for(std::chrono::milliseconds(20));
            drain();
        }
        if (!NET_DVR_StopRemoteConfig(handle)) { complete = false; error = NET_DVR_GetLastError(); }
    }
    emit("{\"kind\":\"historyEnd\",\"request\":" + std::to_string(request) +
        ",\"complete\":" + (complete ? "true" : "false") + ",\"error\":" + std::to_string(error) +
        ",\"count\":" + std::to_string(count) + "}");
}
int main() {
    std::ios::sync_with_stdio(false); std::cin.tie(nullptr);
    struct rlimit core_limit{0, 0}; setrlimit(RLIMIT_CORE, &core_limit);
    std::signal(SIGTERM, stop_signal); std::signal(SIGINT, stop_signal);
    // Secrets arrive on stdin, never argv, environment, source or output.
    std::string host, port, username, password, library_path, timeout;
    if (!std::getline(std::cin, host) || !std::getline(std::cin, port) ||
        !std::getline(std::cin, username) || !std::getline(std::cin, password) ||
        !std::getline(std::cin, library_path) || !std::getline(std::cin, timeout)) return 2;
    NET_DVR_USER_LOGIN_INFO login{};
    if (host.size() >= sizeof(login.sDeviceAddress) || username.size() >= sizeof(login.sUserName) ||
        password.size() >= sizeof(login.sPassword)) { status("login", false, NET_DVR_PARAMETER_ERROR); return 2; }
    NET_DVR_LOCAL_SDK_PATH path{};
    if (library_path.size() >= sizeof(path.sPath)) return 2;
    std::memcpy(path.sPath, library_path.c_str(), library_path.size());
    std::string crypto = library_path + "/libcrypto.so.1.1", ssl = library_path + "/libssl.so.1.1";
    if (!NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, &path) ||
        !NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, &crypto[0]) ||
        !NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, &ssl[0]) || !NET_DVR_Init()) {
        status("initialize", false, NET_DVR_GetLastError());
        status("cleanup", NET_DVR_Cleanup()); return 2;
    }
    status("initialize", true);
    emit("{\"kind\":\"sdk\",\"version\":" + std::to_string(NET_DVR_GetSDKBuildVersion()) + "}");
    unsigned timeout_ms = std::min(10000u, std::max(100u, static_cast<unsigned>(std::stoul(timeout))));
    NET_DVR_SetConnectTime(timeout_ms, 1); NET_DVR_SetRecvTimeOut(timeout_ms);
    NET_DVR_SetReconnect(30000, FALSE); // Application owns bounded reconnect; never auto-retry bad credentials.
    std::memcpy(login.sDeviceAddress, host.c_str(), host.size());
    std::memcpy(login.sUserName, username.c_str(), username.size());
    std::memcpy(login.sPassword, password.c_str(), password.size());
    login.wPort = static_cast<WORD>(std::stoul(port)); login.bUseAsynLogin = FALSE; login.byLoginMode = 0;
    NET_DVR_DEVICEINFO_V40 info{};
    LONG user = NET_DVR_Login_V40(&login, &info);
    std::memset(login.sPassword, 0, sizeof(login.sPassword)); std::fill(password.begin(), password.end(), '\0');
    if (user < 0) {
        status("login", false, NET_DVR_GetLastError());
        status("cleanup", NET_DVR_Cleanup()); return 2;
    }
    status("login", true);
    emit("{\"kind\":\"identity\",\"serial\":" + quote(text(info.struDeviceV30.sSerialNumber, SERIALNO_LEN)) + "}");
    NET_DVR_DEVICECFG_V40 device{}; device.dwSize = sizeof(device); DWORD returned = 0;
    if (NET_DVR_GetDVRConfig(user, NET_DVR_GET_DEVICECFG_V40, 0, &device, sizeof(device), &returned)) {
        emit("{\"kind\":\"device\",\"model\":" + quote(text(device.byDevTypeName, DEV_TYPE_NAME_LEN)) +
            ",\"firmwareRaw\":" + std::to_string(device.dwSoftwareVersion) +
            ",\"buildDateRaw\":" + std::to_string(device.dwSoftwareBuildDate) + "}");
    } else status("device", false, NET_DVR_GetLastError());
    NET_DVR_TIME device_time{};
    if (NET_DVR_GetDVRConfig(user, NET_DVR_GET_TIMECFG, 0, &device_time, sizeof(device_time), &returned))
        emit("{\"kind\":\"deviceTime\",\"time\":" + time_json(device_time) + "}");
    else status("deviceTime", false, NET_DVR_GetLastError());
    std::string input = "<AcsAbility version=\"2.0\"></AcsAbility>";
    std::array<char, 65536> ability{};
    if (NET_DVR_GetDeviceAbility(user, ACS_ABILITY, &input[0], input.size(), ability.data(), ability.size()))
        emit("{\"kind\":\"capabilities\",\"xml\":" + quote(text(ability.data(), ability.size())) + "}");
    else status("capabilities", false, NET_DVR_GetLastError());
    // Real-time subscription coexists with the legacy client/iVMS channel.
    // Recovery comes from our durable history query, not offline alarm replay.
    NET_DVR_SETUPALARM_PARAM alarm{}; alarm.dwSize = sizeof(alarm); alarm.byLevel = 2; alarm.byDeployType = 1;
    LONG alarm_handle = -1;
    if (NET_DVR_SetDVRMessageCallBack_V31(alarm_callback, nullptr) &&
        NET_DVR_SetExceptionCallBack_V30(0, nullptr, exception_callback, nullptr))
    {
        alarm_handle = NET_DVR_SetupAlarmChan_V41(user, &alarm);
        if (alarm_handle < 0 && NET_DVR_GetLastError() == NET_DVR_NOSUPPORT) {
            alarm.byDeployType = 0;
            alarm_handle = NET_DVR_SetupAlarmChan_V41(user, &alarm);
        }
    }
    status("subscription", alarm_handle >= 0, alarm_handle < 0 ? NET_DVR_GetLastError() : 0);
    emit("{\"kind\":\"subscriptionMode\",\"deployType\":" + std::to_string(alarm.byDeployType) + "}");
    emit("{\"kind\":\"ready\"}");
    auto heartbeat = Clock::now();
    while (!stopping) {
        drain();
        if (Clock::now() - heartbeat > std::chrono::seconds(2)) {
            emit("{\"kind\":\"heartbeat\"}"); heartbeat = Clock::now();
        }
        // iostream may buffer subsequent command lines; poll only when its buffer is empty.
        pollfd fd{STDIN_FILENO, POLLIN, 0};
        if (std::cin.rdbuf()->in_avail() > 0 || poll(&fd, 1, 100) > 0) {
            std::string line;
            if (!std::getline(std::cin, line) || line == "QUIT") break;
            std::istringstream command(line); std::string name; command >> name;
            if (name == "HISTORY") history(user, command, std::max(10000u, timeout_ms * 3));
        }
    }
    if (alarm_handle >= 0) status("unsubscribe", NET_DVR_CloseAlarmChan_V30(alarm_handle));
    drain();
    status("logout", NET_DVR_Logout(user)); status("cleanup", NET_DVR_Cleanup());
    return 0;
}
