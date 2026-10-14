// Door-duration configuration is separate from the card executable.
static std::string door_cfg_hex(const NET_DVR_DOOR_CFG &cfg) {
    const BYTE *p=reinterpret_cast<const BYTE *>(&cfg); const char *digits="0123456789abcdef"; std::string out;
    for(size_t i=0;i<sizeof(cfg);++i){out+=digits[p[i]>>4];out+=digits[p[i]&15];} return out;
}
static void configure_door_duration(LONG user,const NET_DVR_DOOR_CFG &door,NET_DVR_DOOR_CFG &current,bool complete,BOOL read,DWORD count) {
          bool reserved_zero=current.byRes1==0 && std::all_of(std::begin(current.byRes2),std::end(current.byRes2),[](BYTE v){return v==0;});
          if(!complete || !read || count!=sizeof(current) || current.dwSize!=sizeof(current) ||
             std::memcmp(&current,&door,sizeof(current))!=0 || !reserved_zero) {
            emit("{\"kind\":\"durationResult\",\"sent\":false,\"ok\":false,\"code\":\"DOOR_BASELINE_OR_RESERVED_FIELDS_UNVERIFIED\"}");
          } else {
            NET_DVR_DOOR_CFG expected=current; expected.byOpenDuration=5;
            BOOL set=NET_DVR_SetDVRConfig(user,NET_DVR_SET_DOOR_CFG,1,&expected,sizeof(expected));
            DWORD error=set?0:NET_DVR_GetLastError();
            NET_DVR_DOOR_CFG actual{}; actual.dwSize=sizeof(actual); DWORD length=0;
            BOOL got=NET_DVR_GetDVRConfig(user,NET_DVR_GET_DOOR_CFG,1,&actual,sizeof(actual),&length);
            bool verified=set && got && length==sizeof(actual) && actual.dwSize==sizeof(actual) &&
                std::memcmp(&expected,&actual,sizeof(actual))==0;
            emit("{\"kind\":\"durationResult\",\"sent\":true,\"ok\":"+std::string(set?"true":"false")+
                ",\"error\":"+std::to_string(error)+",\"readbackVerified\":"+std::string(verified?"true":"false")+
                ",\"beforeHex\":"+quote(door_cfg_hex(door))+",\"expectedHex\":"+quote(door_cfg_hex(expected))+
                ",\"actualHex\":"+quote(door_cfg_hex(actual))+"}");
          }

}
