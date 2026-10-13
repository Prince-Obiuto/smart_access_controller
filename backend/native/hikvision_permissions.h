// Our protocol implementation, using only declarations from the owner's header.
// Card callbacks follow the package's Manage Card Information example. Schedule
// commands follow Configure Access Permission Control Schedule (legacy getters).
static std::mutex card_mutex;
static std::array<NET_DVR_CARD_CFG_V50, 128> card_queue;
#ifdef IFT_VALIDITY_EVIDENCE
static std::array<DWORD, 128> card_lengths;
static void validity_evidence(const NET_DVR_CARD_CFG_V50 &c, unsigned request, bool write, DWORD length) {
    const BYTE *bytes = reinterpret_cast<const BYTE *>(&c.struValid);
    const char *digits = "0123456789abcdef";
    std::string hex;
    for (size_t i = 0; i < sizeof(c.struValid); ++i) { hex += digits[bytes[i] >> 4]; hex += digits[bytes[i] & 15]; }
    emit("{\"kind\":\"validityBytes\",\"request\":" + std::to_string(request) +
         ",\"command\":" + std::to_string(write ? NET_DVR_SET_CARD_CFG_V50 : NET_DVR_GET_CARD_CFG_V50) +
         ",\"direction\":" + quote(write ? "WRITE" : "READ") + ",\"card\":" + quote(text(c.byCardNo, ACS_CARD_NO_LEN)) +
         ",\"nativeSize\":" + std::to_string(sizeof(c)) + ",\"declaredSize\":" + std::to_string(c.dwSize) +
         ",\"callbackLength\":" + std::to_string(length) +
         ",\"validityOffset\":" + std::to_string(offsetof(NET_DVR_CARD_CFG_V50, struValid)) +
         ",\"validitySize\":" + std::to_string(sizeof(c.struValid)) + ",\"hex\":" + quote(hex) + "}");
}
#endif
static size_t card_count = 0;
static std::atomic<DWORD> card_status{0}, card_error{0};
static std::atomic<bool> card_gap{false};
static void CALLBACK card_callback(DWORD type, void *buffer, DWORD length, void *) {
    if (!buffer) { card_gap = true; return; }
    if (type == NET_SDK_CALLBACK_TYPE_STATUS) {
        if (length < sizeof(DWORD)) { card_gap = true; return; }
        DWORD value = 0; std::memcpy(&value, buffer, sizeof(value));
        if (value == NET_SDK_CALLBACK_STATUS_FAILED && length >= 2 * sizeof(DWORD)) {
            DWORD error = 0; std::memcpy(&error, static_cast<char *>(buffer) + sizeof(DWORD), sizeof(error));
            card_error = error;
        }
        card_status = value;
    } else if (type == NET_SDK_CALLBACK_TYPE_DATA) {
        if (length < sizeof(NET_DVR_CARD_CFG_V50)) { card_gap = true; return; }
        std::unique_lock<std::mutex> lock(card_mutex, std::try_to_lock);
        if (!lock.owns_lock() || card_count == card_queue.size()) { card_gap = true; return; }
        std::memcpy(&card_queue[card_count++], buffer, sizeof(NET_DVR_CARD_CFG_V50));
#ifdef IFT_VALIDITY_EVIDENCE
        card_lengths[card_count - 1] = length;
#endif
    }
}
static std::string ex_time(const NET_DVR_TIME_EX &t) {
    return "[" + std::to_string(t.wYear) + "," + std::to_string(t.byMonth) + "," + std::to_string(t.byDay) +
        "," + std::to_string(t.byHour) + "," + std::to_string(t.byMinute) + "," + std::to_string(t.bySecond) + "]";
}
static std::string card_json(const NET_DVR_CARD_CFG_V50 &c) {
    std::string doors = "[", plans = "[";
    for (unsigned i = 0; i < MAX_DOOR_NUM_256; ++i) {
        if (i) { doors += ','; plans += ','; }
        doors += std::to_string(c.byDoorRight[i]); plans += '[';
        for (unsigned j = 0; j < MAX_CARD_RIGHT_PLAN_NUM; ++j) {
            if (j) plans += ',';
            plans += std::to_string(c.wCardRightPlan[i][j]);
        }
        plans += ']';
    }
    return "{\"card\":" + quote(text(c.byCardNo, ACS_CARD_NO_LEN)) + ",\"valid\":" + std::to_string(c.byCardValid) +
        ",\"type\":" + std::to_string(c.byCardType) + ",\"leader\":" + std::to_string(c.byLeaderCard) +
        ",\"doors\":" + doors + "],\"plans\":" + plans + "],\"validityEnabled\":" + std::to_string(c.struValid.byEnable) +
        ",\"beginFlag\":" + std::to_string(c.struValid.byBeginTimeFlag) + ",\"endFlag\":" + std::to_string(c.struValid.byEnableTimeFlag) +
        ",\"timeType\":" + std::to_string(c.struValid.byTimeType) + ",\"start\":" + ex_time(c.struValid.struBeginTime) +
        ",\"expiry\":" + ex_time(c.struValid.struEndTime) + "}";
}
static void permission_end(unsigned request, const std::string &code, DWORD error = 0) {
    emit("{\"kind\":\"permissionEnd\",\"request\":" + std::to_string(request) +
         ",\"code\":" + quote(code) + ",\"error\":" + std::to_string(error) + "}");
}
static bool card_number(const std::string &s) {
    return !s.empty() && s.size() <= ACS_CARD_NO_LEN &&
        std::all_of(s.begin(), s.end(), [](char c) { return c >= '0' && c <= '9'; });
}
static std::vector<std::string> allowed_cards;
static std::vector<unsigned> allowed_plans;
static void card_request(LONG user, unsigned request, const std::string &number,
                         NET_DVR_CARD_CFG_V50 *write, unsigned timeout_ms) {
    card_count = 0; card_gap = false; card_status = 0; card_error = 0;
    NET_DVR_CARD_CFG_COND cond{}; cond.dwSize = sizeof(cond);
    cond.dwCardNum = number == "ALL" ? 0xffffffff : 1; cond.byCheckCardNo = 1;
    LONG handle = NET_DVR_StartRemoteConfig(user, write ? NET_DVR_SET_CARD_CFG_V50 : NET_DVR_GET_CARD_CFG_V50,
        &cond, sizeof(cond), card_callback, nullptr);
    DWORD error = handle < 0 ? NET_DVR_GetLastError() : 0;
    if (handle < 0) { permission_end(request, "SDK_ERROR", error); return; }
    if (number != "ALL") {
#ifdef IFT_VALIDITY_EVIDENCE
        if (write) validity_evidence(*write, request, true, sizeof(*write));
#endif
        NET_DVR_CARD_CFG_SEND_DATA send{}; send.dwSize = sizeof(send);
        std::memcpy(send.byCardNo, number.data(), number.size());
        if (!NET_DVR_SendRemoteConfig(handle, ENUM_ACS_SEND_DATA,
            write ? reinterpret_cast<char *>(write) : reinterpret_cast<char *>(&send), write ? sizeof(*write) : sizeof(send)))
            error = NET_DVR_GetLastError();
    }
    auto deadline = Clock::now() + std::chrono::milliseconds(std::max(10000u, timeout_ms * 3));
    unsigned total = 0;
    do {
        std::array<NET_DVR_CARD_CFG_V50, 128> batch;
#ifdef IFT_VALIDITY_EVIDENCE
        std::array<DWORD, 128> lengths;
#endif
        size_t count;
        { std::lock_guard<std::mutex> lock(card_mutex); count = card_count;
          std::copy_n(card_queue.begin(), count, batch.begin());
#ifdef IFT_VALIDITY_EVIDENCE
          std::copy_n(card_lengths.begin(), count, lengths.begin());
#endif
          card_count = 0; }
        for (size_t i = 0; i < count; ++i) {
            if (++total > 10000) { card_gap = true; break; }
            emit("{\"kind\":\"permissionRow\",\"request\":" + std::to_string(request) + ",\"data\":" + card_json(batch[i]) + "}");
#ifdef IFT_VALIDITY_EVIDENCE
            validity_evidence(batch[i], request, false, lengths[i]);
#endif
        }
        if (error || card_gap || (card_status != 0 && card_status != NET_SDK_CALLBACK_STATUS_PROCESSING)) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    } while (!stopping && Clock::now() < deadline);
    bool stopped = NET_DVR_StopRemoteConfig(handle);
    // Stop joins remote callbacks; drain the last in-transit records too.
    const size_t remaining = card_count;
    if (remaining + total > 10000) card_gap = true;
    for (size_t i = 0; i < remaining && total < 10000; ++i, ++total) {
        emit("{\"kind\":\"permissionRow\",\"request\":" + std::to_string(request) + ",\"data\":" + card_json(card_queue[i]) + "}");
#ifdef IFT_VALIDITY_EVIDENCE
        validity_evidence(card_queue[i], request, false, card_lengths[i]);
#endif
    }
    if (!stopped) { if (!error) error = NET_DVR_GetLastError(); stopping = 1; }
    if (!error) error = card_error;
    permission_end(request, card_gap ? "CALLBACK_GAP" : error ? "SDK_ERROR" :
        card_status == NET_SDK_CALLBACK_STATUS_SUCCESS && stopped ? "OK" : "UNCERTAIN", error);
}
static std::string daytime(const NET_DVR_SIMPLE_DAYTIME &t) {
    return std::to_string(t.byHour * 3600 + t.byMinute * 60 + t.bySecond);
}
static std::string segment_json(const NET_DVR_SINGLE_PLAN_SEGMENT &s) {
    return "[" + std::to_string(s.byEnable) + "," + daytime(s.struTimeSegment.struBeginTime) + "," +
        daytime(s.struTimeSegment.struEndTime) + "," + std::to_string(s.byDoorStatus) + "," + std::to_string(s.byVerifyMode) + "]";
}
static std::string date_json(const NET_DVR_DATE &d) {
    return "[" + std::to_string(d.wYear) + "," + std::to_string(d.byMonth) + "," + std::to_string(d.byDay) + "]";
}
template <typename T> static std::string numbers(const T &a) {
    std::string s = "["; bool first = true;
    for (auto n : a) { if (!first) s += ','; first = false; s += std::to_string(n); } return s + ']';
}
static bool read_segment(std::istringstream &in, NET_DVR_SINGLE_PLAN_SEGMENT &s) {
    unsigned enabled, begin, end; in >> enabled >> begin >> end;
    if (!in || enabled > 1 || begin > 86399 || end > 86400 || (enabled && begin >= end)) return false;
    s.byEnable = enabled; // Door state and reader verification remain zero: CARD eligibility only.
    s.struTimeSegment.struBeginTime = {BYTE(begin / 3600), BYTE(begin / 60 % 60), BYTE(begin % 60), 0};
    s.struTimeSegment.struEndTime = {BYTE(end / 3600), BYTE(end / 60 % 60), BYTE(end % 60), 0}; return true;
}
static void schedule_request(LONG user, unsigned request, std::istringstream &in, bool write) {
    std::string kind; unsigned id; in >> kind >> id;
    if (!in || id < 1 || id > 255 || (write && std::find(allowed_plans.begin(), allowed_plans.end(), id) == allowed_plans.end())) {
        permission_end(request, "SCOPE_BLOCKED"); return;
    }
    DWORD returned = 0, error = 0; bool ok = false; std::string data;
    if (kind == "WEEK") {
        NET_DVR_WEEK_PLAN_CFG c{}; c.dwSize = sizeof(c); c.byEnable = 1;
        if (write) {
            for (auto &day : c.struPlanCfg) for (auto &s : day) if (!read_segment(in, s)) { permission_end(request, "INVALID_INPUT"); return; }
            ok = NET_DVR_SetDVRConfig(user, NET_DVR_SET_CARD_RIGHT_WEEK_PLAN, id, &c, sizeof(c));
        } else ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_WEEK_PLAN, id, &c, sizeof(c), &returned);
        if (!write && ok && returned != sizeof(c)) { permission_end(request, "NATIVE_SIZE_MISMATCH"); return; }
        data = "{\"enabled\":" + std::to_string(c.byEnable) + ",\"days\":[";
        for (unsigned d = 0; d < MAX_DAYS; ++d) { if (d) data += ','; data += '[';
            for (unsigned i = 0; i < MAX_TIMESEGMENT_V30; ++i) { if (i) data += ','; data += segment_json(c.struPlanCfg[d][i]); } data += ']'; } data += "]}";
    } else if (kind == "TEMPLATE" || (kind == "TEMPLATE_V50" && !write)) {
        NET_DVR_PLAN_TEMPLATE c{}; c.dwSize = sizeof(c); c.byEnable = 1;
        if (write) {
            in >> c.dwWeekPlanNo;
            for (auto &n : c.dwHolidayGroupNo) in >> n;
            if (!in || std::find(allowed_plans.begin(), allowed_plans.end(), c.dwWeekPlanNo) == allowed_plans.end()) { permission_end(request, "SCOPE_BLOCKED"); return; }
            for (auto n : c.dwHolidayGroupNo) if (n && std::find(allowed_plans.begin(), allowed_plans.end(), n) == allowed_plans.end()) { permission_end(request, "SCOPE_BLOCKED"); return; }
            const std::string label = "IFT TEST " + std::to_string(id); std::memcpy(c.byTemplateName, label.data(), label.size());
            ok = NET_DVR_SetDVRConfig(user, NET_DVR_SET_CARD_RIGHT_PLAN_TEMPLATE, id, &c, sizeof(c));
        } else if (kind == "TEMPLATE_V50") {
            NET_DVR_PLAN_TEMPLATE_COND condition{}; condition.dwSize = sizeof(condition); condition.dwPlanTemplateNumber = id;
            DWORD item_status = 0;
            ok = NET_DVR_GetDeviceConfig(user, NET_DVR_GET_CARD_RIGHT_PLAN_TEMPLATE_V50, 1, &condition, sizeof(condition), &item_status, &c, sizeof(c));
            if (ok && item_status != 0) { permission_end(request, "SDK_ERROR", item_status); return; }
            returned = sizeof(c);
        } else ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_PLAN_TEMPLATE, id, &c, sizeof(c), &returned);
        if (!write && ok && returned != sizeof(c)) { permission_end(request, "NATIVE_SIZE_MISMATCH"); return; }
        data = "{\"enabled\":" + std::to_string(c.byEnable) + ",\"name\":" + quote(text(c.byTemplateName, TEMPLATE_NAME_LEN)) +
            ",\"week\":" + std::to_string(c.dwWeekPlanNo) + ",\"groups\":" + numbers(c.dwHolidayGroupNo) + "}";
    } else if (kind == "HOLIDAY") {
        NET_DVR_HOLIDAY_PLAN_CFG c{}; c.dwSize = sizeof(c); c.byEnable = 1;
        if (write) {
            unsigned y, m, d; in >> y >> m >> d; c.struBeginDate = {WORD(y), BYTE(m), BYTE(d)}; c.struEndDate = c.struBeginDate;
            for (auto &s : c.struPlanCfg) if (!read_segment(in, s)) { permission_end(request, "INVALID_INPUT"); return; }
            ok = NET_DVR_SetDVRConfig(user, NET_DVR_SET_CARD_RIGHT_HOLIDAY_PLAN, id, &c, sizeof(c));
        } else ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_HOLIDAY_PLAN, id, &c, sizeof(c), &returned);
        if (!write && ok && returned != sizeof(c)) { permission_end(request, "NATIVE_SIZE_MISMATCH"); return; }
        data = "{\"enabled\":" + std::to_string(c.byEnable) + ",\"start\":" + date_json(c.struBeginDate) + ",\"end\":" + date_json(c.struEndDate) + ",\"segments\":[";
        for (unsigned i = 0; i < MAX_TIMESEGMENT_V30; ++i) { if (i) data += ','; data += segment_json(c.struPlanCfg[i]); } data += "]}";
    } else if (kind == "GROUP") {
        NET_DVR_HOLIDAY_GROUP_CFG c{}; c.dwSize = sizeof(c); c.byEnable = 1;
        if (write) {
            for (auto &n : c.dwHolidayPlanNo) { in >> n;
                if (n && std::find(allowed_plans.begin(), allowed_plans.end(), n) == allowed_plans.end()) { permission_end(request, "SCOPE_BLOCKED"); return; } }
            if (!in) { permission_end(request, "INVALID_INPUT"); return; }
            const std::string label = "IFT TEST " + std::to_string(id); std::memcpy(c.byGroupName, label.data(), label.size());
            ok = NET_DVR_SetDVRConfig(user, NET_DVR_SET_CARD_RIGHT_HOLIDAY_GROUP, id, &c, sizeof(c));
        } else ok = NET_DVR_GetDVRConfig(user, NET_DVR_GET_CARD_RIGHT_HOLIDAY_GROUP, id, &c, sizeof(c), &returned);
        if (!write && ok && returned != sizeof(c)) { permission_end(request, "NATIVE_SIZE_MISMATCH"); return; }
        data = "{\"enabled\":" + std::to_string(c.byEnable) + ",\"plans\":" + numbers(c.dwHolidayPlanNo) + "}";
    } else { permission_end(request, "INVALID_INPUT"); return; }
    if (!ok) error = NET_DVR_GetLastError();
    if (ok && !write) emit("{\"kind\":\"permissionRow\",\"request\":" + std::to_string(request) + ",\"data\":" + data + "}");
    permission_end(request, ok ? "OK" : "SDK_ERROR", error);
}
static void permission_command(LONG user, const std::string &name, std::istringstream &in, unsigned timeout_ms) {
    unsigned request = 0; in >> request;
    if (name == "AUTHORIZE") {
        allowed_cards.clear(); allowed_plans.clear(); unsigned count = 0; in >> count;
        if (count > 32) { permission_end(request, "INVALID_INPUT"); return; }
        for (unsigned i = 0; i < count; ++i) { std::string card; in >> card; if (!card_number(card)) { permission_end(request, "INVALID_INPUT"); return; } allowed_cards.push_back(card); }
        in >> count; if (count > 64) { permission_end(request, "INVALID_INPUT"); return; }
        for (unsigned i = 0; i < count; ++i) { unsigned n; in >> n; if (n < 1 || n > 255) { permission_end(request, "INVALID_INPUT"); return; } allowed_plans.push_back(n); }
        if (!in) { allowed_cards.clear(); allowed_plans.clear(); permission_end(request, "INVALID_INPUT"); return; }
        permission_end(request, "OK");
    } else if (name == "CARD_READ") {
        std::string card; in >> card;
        if (card != "ALL" && !card_number(card)) { permission_end(request, "INVALID_INPUT"); return; }
        card_request(user, request, card, nullptr, timeout_ms);
    } else if (name == "CARD_WRITE") {
        std::string card; unsigned enabled, plan; in >> card >> enabled >> plan;
        if (!card_number(card) || enabled > 1 || std::find(allowed_cards.begin(), allowed_cards.end(), card) == allowed_cards.end() ||
            (enabled && std::find(allowed_plans.begin(), allowed_plans.end(), plan) == allowed_plans.end())) { permission_end(request, "SCOPE_BLOCKED"); return; }
        NET_DVR_CARD_CFG_V50 c{}; c.dwSize = sizeof(c);
        std::memcpy(c.byCardNo, card.data(), card.size());
        // Disable removes all door rights, without deleting biometric/person records.
        c.dwModifyParamType = CARD_PARAM_DOOR_RIGHT | CARD_PARAM_CARD_TYPE;
        c.byCardType = 3; // Advertised blacklist type, plus zero rights on every door.
        if (enabled) {
            c.dwModifyParamType |= CARD_PARAM_CARD_VALID | CARD_PARAM_VALID | CARD_PARAM_CARD_TYPE | CARD_PARAM_RIGHT_PLAN | CARD_PARAM_LEADER_CARD;
            c.byCardValid = 1; c.byCardType = 1; c.byDoorRight[0] = 1; c.wCardRightPlan[0][0] = plan;
            c.struValid.byEnable = 1; c.struValid.byBeginTimeFlag = 1; c.struValid.byEnableTimeFlag = 1; c.struValid.byTimeType = 0;
            auto read = [&in](NET_DVR_TIME_EX &t) { unsigned y,m,d,h,n,s; in >> y >> m >> d >> h >> n >> s;
                if (!in || y < 1970 || y > 2037 || m < 1 || m > 12 || d < 1 || d > 31 || h > 23 || n > 59 || s > 59) return false;
                t = {WORD(y), BYTE(m), BYTE(d), BYTE(h), BYTE(n), BYTE(s), 0}; return true; };
            if (!read(c.struValid.struBeginTime) || !read(c.struValid.struEndTime)) { permission_end(request, "INVALID_INPUT"); return; }
        }
        card_request(user, request, card, &c, timeout_ms);
    } else if (name == "PLAN_READ" || name == "PLAN_WRITE") schedule_request(user, request, in, name == "PLAN_WRITE");
    else permission_end(request, "INVALID_INPUT");
}
