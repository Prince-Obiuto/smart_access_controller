// Authenticated application management. Types/commands come from HCNetSDK.h.
// PREPARE never writes. APPLY retains the entire typed preimage, including reserved bytes.
static std::string plan_json(const NET_DVR_WEEK_PLAN_CFG &c) {
    std::string out="{\"enabled\":"+std::to_string(c.byEnable)+",\"days\":[";
    for(unsigned d=0;d<MAX_DAYS;++d) {if(d)out+=',';out+='[';
        for(unsigned i=0;i<MAX_TIMESEGMENT_V30;++i) {if(i)out+=',';out+=segment_json(c.struPlanCfg[d][i]);}out+=']';}return out+"]}";
}
static std::string plan_json(const NET_DVR_PLAN_TEMPLATE &c) {
    return "{\"enabled\":"+std::to_string(c.byEnable)+",\"name\":"+quote(text(c.byTemplateName,TEMPLATE_NAME_LEN))+
        ",\"week\":"+std::to_string(c.dwWeekPlanNo)+",\"groups\":"+numbers(c.dwHolidayGroupNo)+"}";
}
static std::string plan_json(const NET_DVR_HOLIDAY_GROUP_CFG &c) {
    return "{\"enabled\":"+std::to_string(c.byEnable)+",\"name\":"+quote(text(c.byGroupName,HOLIDAY_GROUP_NAME_LEN))+
        ",\"plans\":"+numbers(c.dwHolidayPlanNo)+"}";
}
static std::string plan_json(const NET_DVR_HOLIDAY_PLAN_CFG &c) {
    std::string out="{\"enabled\":"+std::to_string(c.byEnable)+",\"start\":"+date_json(c.struBeginDate)+",\"end\":"+date_json(c.struEndDate)+",\"segments\":[";
    for(unsigned i=0;i<MAX_TIMESEGMENT_V30;++i) {if(i)out+=',';out+=segment_json(c.struPlanCfg[i]);}return out+"]}";
}
static bool patch_segment(std::istringstream &in, NET_DVR_SINGLE_PLAN_SEGMENT &s) {
    unsigned enabled,begin,end;in>>enabled>>begin>>end;
    if(!in||enabled>1||begin>86399||end>86400||(enabled&&begin>=end))return false;
    s.byEnable=enabled;
    auto set=[](NET_DVR_SIMPLE_DAYTIME &t,unsigned n){t.byHour=n/3600;t.byMinute=n/60%60;t.bySecond=n%60;};
    set(s.struTimeSegment.struBeginTime,begin);set(s.struTimeSegment.struEndTime,end);return true;
}
static bool patch_plan(std::istringstream &in,NET_DVR_WEEK_PLAN_CFG &c) {
    for(auto &day:c.struPlanCfg) { for(auto &s:day) { if(!patch_segment(in,s))return false; } }
    return true;
}
static bool patch_plan(std::istringstream &in,NET_DVR_HOLIDAY_PLAN_CFG &c) {
    unsigned y,m,d,y2,m2,d2;in>>y>>m>>d>>y2>>m2>>d2;
    if(!in||y<1970||y>2037||y2<1970||y2>2037||m<1||m>12||m2<1||m2>12||d<1||d>31||d2<1||d2>31)return false;
    c.struBeginDate={WORD(y),BYTE(m),BYTE(d)};c.struEndDate={WORD(y2),BYTE(m2),BYTE(d2)};
    for(auto &s:c.struPlanCfg) { if(!patch_segment(in,s))return false; }
    return true;
}
static bool patch_plan(std::istringstream &in,NET_DVR_HOLIDAY_GROUP_CFG &c) {
    for(auto &n:c.dwHolidayPlanNo)in>>n;
    return bool(in);
}
static bool patch_plan(std::istringstream &in,NET_DVR_PLAN_TEMPLATE &c) {
    in>>c.dwWeekPlanNo;for(auto &n:c.dwHolidayGroupNo)in>>n;return bool(in);
}
template<typename T> static void management_plan(LONG user,unsigned request,std::istringstream &in,
    const std::string &action,unsigned id,DWORD get,DWORD set) {
    T current{};current.dwSize=sizeof(current);DWORD returned=0;
    if(!NET_DVR_GetDVRConfig(user,get,id,&current,sizeof(current),&returned)) {permission_end(request,"SDK_ERROR",NET_DVR_GetLastError());return;}
    if(returned!=sizeof(current)||current.dwSize!=sizeof(current)) {permission_end(request,"NATIVE_SIZE_MISMATCH");return;}
    T result=current;
    if(action=="PREPARE") {
        std::string before;unsigned enabled;in>>before>>enabled;
        if(before!=native_hex(current)) {permission_end(request,"REQUIRES_RECONCILIATION");return;}
        if(enabled>1||!patch_plan(in,result)) {permission_end(request,"INVALID_INPUT");return;}result.byEnable=enabled;
    } else if(action=="APPLY") {
        std::string before,after;in>>before>>after;
        if(before!=native_hex(current)) {permission_end(request,"REQUIRES_RECONCILIATION");return;}
        if(std::find(allowed_plans.begin(),allowed_plans.end(),id)==allowed_plans.end()||!native_unhex(after,result)) {permission_end(request,"SCOPE_BLOCKED");return;}
        if(!NET_DVR_SetDVRConfig(user,set,id,&result,sizeof(result))) {permission_end(request,"SDK_ERROR",NET_DVR_GetLastError());return;}
    } else if(action!="READ") {permission_end(request,"INVALID_INPUT");return;}
    emit("{\"kind\":\"permissionRow\",\"request\":"+std::to_string(request)+",\"data\":{\"nativeHex\":"+quote(native_hex(result))+",\"record\":"+plan_json(result)+"}}");
    permission_end(request,"OK");
}
static void management_command(LONG user,unsigned request,std::istringstream &in,unsigned timeout_ms) {
    std::string action,kind;in>>action>>kind;
    if(kind=="CARD") {
        std::string number,hex;in>>number;
        if(action=="READ") {if(number!="ALL"&&!card_number(number)) {permission_end(request,"INVALID_INPUT");return;}card_request(user,request,number,nullptr,timeout_ms,true);return;}
        std::string before; if(action=="APPLY")in>>before;
        in>>hex;
        if(!card_number(number)) {permission_end(request,"INVALID_INPUT");return;}
        NET_DVR_CARD_CFG_V50 c{};c.dwSize=sizeof(c);
        if(hex!="-"&&!native_unhex(hex,c)) {permission_end(request,"INVALID_INPUT");return;}
        if(hex!="-"&&text(c.byCardNo,ACS_CARD_NO_LEN)!=number) {permission_end(request,"INVALID_INPUT");return;}
        if(action=="PREPARE") {
            unsigned enabled,plan;in>>enabled>>plan;
            if(enabled>1||plan>65535||(!plan&&enabled)) {permission_end(request,"INVALID_INPUT");return;}
            std::memset(c.byCardNo,0,sizeof(c.byCardNo));std::memcpy(c.byCardNo,number.data(),number.size());
            c.dwModifyParamType=CARD_PARAM_DOOR_RIGHT|CARD_PARAM_CARD_TYPE;
            c.byCardType=enabled?1:3;
            if(!enabled) std::memset(c.byDoorRight,0,sizeof(c.byDoorRight));
            else {
                c.dwModifyParamType|=CARD_PARAM_CARD_VALID|CARD_PARAM_VALID|CARD_PARAM_RIGHT_PLAN|CARD_PARAM_LEADER_CARD;
                c.byCardValid=1;c.byLeaderCard=0;c.byDoorRight[0]=1;
                std::memset(c.wCardRightPlan[0],0,sizeof(c.wCardRightPlan[0]));c.wCardRightPlan[0][0]=plan;
                c.struValid.byEnable=1;c.struValid.byBeginTimeFlag=1;c.struValid.byEnableTimeFlag=1;c.struValid.byTimeType=0;
                auto time=[&in](NET_DVR_TIME_EX &t){unsigned y,m,d,h,n,s;in>>y>>m>>d>>h>>n>>s;
                    if(!in||y<1970||y>2037||m<1||m>12||d<1||d>31||h>23||n>59||s>59)return false;
                    t.wYear=y;t.byMonth=m;t.byDay=d;t.byHour=h;t.byMinute=n;t.bySecond=s;return true;};
                if(!time(c.struValid.struBeginTime)||!time(c.struValid.struEndTime)) {permission_end(request,"INVALID_INPUT");return;}
            }
            emit("{\"kind\":\"permissionRow\",\"request\":"+std::to_string(request)+",\"data\":"+card_json(c,true)+"}");permission_end(request,"OK");
        } else if(action=="APPLY") {
            if(std::find(allowed_cards.begin(),allowed_cards.end(),number)==allowed_cards.end()||hex=="-") {permission_end(request,"SCOPE_BLOCKED");return;}
            std::vector<NET_DVR_CARD_CFG_V50> current;
            DWORD error=card_request(user,request,number,nullptr,timeout_ms,false,true,&current);
            bool same=before=="-" ? error==1903&&current.empty() : error==0&&current.size()==1&&native_hex(current[0])==before;
            if(!same) {permission_end(request,error&&error!=1903?"SDK_ERROR":"REQUIRES_RECONCILIATION",error);return;}
            card_request(user,request,number,&c,timeout_ms);
        } else permission_end(request,"INVALID_INPUT");return;
    }
    unsigned id;in>>id;
    if(!in||id<1||id>65535) {permission_end(request,"INVALID_INPUT");return;}
    if(kind=="WEEK")management_plan<NET_DVR_WEEK_PLAN_CFG>(user,request,in,action,id,NET_DVR_GET_CARD_RIGHT_WEEK_PLAN,NET_DVR_SET_CARD_RIGHT_WEEK_PLAN);
    else if(kind=="TEMPLATE")management_plan<NET_DVR_PLAN_TEMPLATE>(user,request,in,action,id,NET_DVR_GET_CARD_RIGHT_PLAN_TEMPLATE,NET_DVR_SET_CARD_RIGHT_PLAN_TEMPLATE);
    else if(kind=="GROUP")management_plan<NET_DVR_HOLIDAY_GROUP_CFG>(user,request,in,action,id,NET_DVR_GET_CARD_RIGHT_HOLIDAY_GROUP,NET_DVR_SET_CARD_RIGHT_HOLIDAY_GROUP);
    else if(kind=="HOLIDAY")management_plan<NET_DVR_HOLIDAY_PLAN_CFG>(user,request,in,action,id,NET_DVR_GET_CARD_RIGHT_HOLIDAY_PLAN,NET_DVR_SET_CARD_RIGHT_HOLIDAY_PLAN);
    else if(kind=="DOOR"&&action=="READ") {
        NET_DVR_DOOR_CFG c{};c.dwSize=sizeof(c);DWORD n=0;
        if(!NET_DVR_GetDVRConfig(user,NET_DVR_GET_DOOR_CFG,id,&c,sizeof(c),&n))permission_end(request,"SDK_ERROR",NET_DVR_GetLastError());
        else if(n!=sizeof(c))permission_end(request,"NATIVE_SIZE_MISMATCH");
        else {emit("{\"kind\":\"permissionRow\",\"request\":"+std::to_string(request)+",\"data\":{\"nativeHex\":"+quote(native_hex(c))+"}}");permission_end(request,"OK");}
    } else permission_end(request,"INVALID_INPUT");
}
