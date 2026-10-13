// Isolated card-only diagnostic bridge. Never dispatches schedule setters.
// An authenticated parent verifies scope/baselines before supplying one command.
#include "HCNetSDK.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <cstddef>
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
#define IFT_VALIDITY_EVIDENCE
#include "hikvision_permissions.h"
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
    DWORD returned = 0;
    NET_DVR_DEVICECFG_V40 device{}; device.dwSize = sizeof(device);
    if (!NET_DVR_GetDVRConfig(user, NET_DVR_GET_DEVICECFG_V40, 0, &device, sizeof(device), &returned) || returned != sizeof(device)) {
        stage("device", FALSE); NET_DVR_Logout(user); NET_DVR_Cleanup(); return 2;
    }
    emit("{\"kind\":\"device\",\"model\":" + quote(text(device.byDevTypeName, DEV_TYPE_NAME_LEN)) +
         ",\"serial\":" + quote(text(info.struDeviceV30.sSerialNumber, SERIALNO_LEN)) +
         ",\"firmware\":" + std::to_string(device.dwSoftwareVersion) +
         ",\"build\":" + std::to_string(device.dwSoftwareBuildDate) + "}");
    NET_DVR_TIME clock{};
    if (!NET_DVR_GetDVRConfig(user, NET_DVR_GET_TIMECFG, 0, &clock, sizeof(clock), &returned) || returned != sizeof(clock)) {
        stage("clock", FALSE); NET_DVR_Logout(user); NET_DVR_Cleanup(); return 2;
    }
    emit("{\"kind\":\"clock\",\"time\":[" + std::to_string(clock.dwYear) + "," + std::to_string(clock.dwMonth) + "," +
        std::to_string(clock.dwDay) + "," + std::to_string(clock.dwHour) + "," + std::to_string(clock.dwMinute) + "," + std::to_string(clock.dwSecond) + "]}");
    auto raw_hex = [](const void *p, size_t n) { const char *digits="0123456789abcdef"; std::string out;
        for (size_t i=0;i<n;++i) { BYTE b=static_cast<const BYTE *>(p)[i]; out+=digits[b>>4];out+=digits[b&15]; } return out; };
    auto resource = [&](const char *kind, LONG id, DWORD command, auto &cfg) {
        cfg.dwSize = sizeof(cfg); DWORD size = 0;
        BOOL ok = NET_DVR_GetDVRConfig(user, command, id, &cfg, sizeof(cfg), &size);
        DWORD error = ok ? 0 : NET_DVR_GetLastError();
        bool complete = ok && size == sizeof(cfg) && cfg.dwSize == sizeof(cfg);
        emit("{\"kind\":\"resource\",\"resource\":" + quote(std::string(kind)+":"+std::to_string(id)) +
            ",\"ok\":" + (complete ? "true" : "false") + ",\"error\":" + std::to_string(error) +
            ",\"returned\":" + std::to_string(size) + ",\"size\":" + std::to_string(sizeof(cfg)) +
            ",\"hex\":" + quote(raw_hex(&cfg,sizeof(cfg))) + "}");
        return complete;
    };
    NET_DVR_PLAN_TEMPLATE plan{};
    bool complete = resource("TEMPLATE", 3, NET_DVR_GET_CARD_RIGHT_PLAN_TEMPLATE, plan);
    emit("{\"kind\":\"templateFields\",\"enabled\":" + std::to_string(plan.byEnable) + ",\"week\":" +
        std::to_string(plan.dwWeekPlanNo) + ",\"name\":" + quote(text(plan.byTemplateName,TEMPLATE_NAME_LEN)) +
        ",\"groups\":" + numbers(plan.dwHolidayGroupNo) + "}");
    if (!complete || !plan.byEnable || plan.dwWeekPlanNo < 1 || plan.dwWeekPlanNo > 64) {
        NET_DVR_Logout(user); NET_DVR_Cleanup(); return 2;
    }
    NET_DVR_WEEK_PLAN_CFG week{};
    complete = resource("WEEK", plan.dwWeekPlanNo, NET_DVR_GET_CARD_RIGHT_WEEK_PLAN, week);
    NET_DVR_DOOR_CFG door{};
    complete = resource("DOOR_CONFIG", 1, NET_DVR_GET_DOOR_CFG, door) && complete;
    std::string days="[";
    for (unsigned d=0;d<MAX_DAYS;++d) { if(d)days+=',';days+='[';
        for(unsigned i=0;i<MAX_TIMESEGMENT_V30;++i){if(i)days+=',';days+=segment_json(week.struPlanCfg[d][i]);}days+=']'; }
    emit("{\"kind\":\"weekFields\",\"enabled\":" + std::to_string(week.byEnable) + ",\"days\":" + days + "]}");
    for (DWORD id : plan.dwHolidayGroupNo) if(id) {
        if(id>64){complete=false;break;}
        NET_DVR_HOLIDAY_GROUP_CFG group{};
        complete = resource("GROUP",id,NET_DVR_GET_CARD_RIGHT_HOLIDAY_GROUP,group) && complete;
        for (DWORD holiday : group.dwHolidayPlanNo) if(holiday) {
            if(holiday>256){complete=false;break;}
            NET_DVR_HOLIDAY_PLAN_CFG h{};
            complete = resource("HOLIDAY",holiday,NET_DVR_GET_CARD_RIGHT_HOLIDAY_PLAN,h) && complete;
        }
    }
    if(!complete){NET_DVR_Logout(user);NET_DVR_Cleanup();return 2;}
    card_request(user, 1, "ALL", nullptr, 10000);
    emit("{\"kind\":\"ready\"}");
    std::string command;
    if (std::getline(std::cin,command) && command != "QUIT") {
        std::istringstream parse(command); std::string name, card; unsigned enabled;
        parse >> name >> card >> enabled;
        const std::vector<std::string> test_cards{"1051305927","1052625607","1052815031"};
        if(name!="CARD" || enabled>1 || (card=="1052815031" && enabled) ||
           std::find(test_cards.begin(),test_cards.end(),card)==test_cards.end()) {
            permission_end(3,"SCOPE_BLOCKED");
        } else {
            // Only card writer is reachable: ordinary type, Door 1, TEMPLATE 3.
            // No generic permission command, schedule writer, clock/door control.
            allowed_cards=test_cards; allowed_plans={3};
            std::string rest;std::getline(parse,rest);
            std::istringstream input("3 " + card + " " + std::to_string(enabled) + " 3" + rest);
            permission_command(user,"CARD_WRITE",input,3000);
            card_request(user,4,card,nullptr,3000); // fresh separate final GET
        }
    }
    bool logout = stage("logout", NET_DVR_Logout(user));
    bool cleanup = stage("cleanup", NET_DVR_Cleanup());
    return logout && cleanup ? 0 : 2;
}
