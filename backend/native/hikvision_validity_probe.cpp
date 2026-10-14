// GET-only ABI/command comparison. Never calls a card or schedule setter.
#define IFT_NO_DIAGNOSTIC_MAIN
#include "hikvision_card_diagnostic.cpp"
#include <type_traits>

static_assert(std::is_standard_layout<NET_DVR_VALID_PERIOD_CFG>::value);
static void abi() {
    emit("{\"kind\":\"abi\",\"validitySize\":" + std::to_string(sizeof(NET_DVR_VALID_PERIOD_CFG)) +
         ",\"validityAlignment\":" + std::to_string(alignof(NET_DVR_VALID_PERIOD_CFG)) +
         ",\"timeExSize\":" + std::to_string(sizeof(NET_DVR_TIME_EX)) +
         ",\"v50Size\":" + std::to_string(sizeof(NET_DVR_CARD_CFG_V50)) +
         ",\"legacySize\":" + std::to_string(sizeof(NET_DVR_CARD_CFG)) +
         ",\"v50ValidityOffset\":" + std::to_string(offsetof(NET_DVR_CARD_CFG_V50,struValid)) +
         ",\"legacyValidityOffset\":" + std::to_string(offsetof(NET_DVR_CARD_CFG,struValid)) +
         ",\"offsets\":{\"enable\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byEnable)) +
         ",\"beginFlag\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byBeginTimeFlag)) +
         ",\"endFlag\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byEnableTimeFlag)) +
         ",\"duration\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byTimeDurationNo)) +
         ",\"start\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,struBeginTime)) +
         ",\"expiry\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,struEndTime)) +
         ",\"timeType\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byTimeType)) +
         ",\"reserved\":" + std::to_string(offsetof(NET_DVR_VALID_PERIOD_CFG,byRes2)) + "}}");
}
static std::mutex old_mutex;
static NET_DVR_CARD_CFG old_card{};
static DWORD old_length=0;
static std::atomic<DWORD> old_status{0}, old_error{0};
static std::atomic<unsigned> old_count{0};
static void CALLBACK legacy_callback(DWORD type, void *buffer, DWORD length, void *) {
    if (!buffer) { old_error=1; return; }
    if (type==NET_SDK_CALLBACK_TYPE_STATUS) {
        if(length<sizeof(DWORD)){old_error=1;return;}
        DWORD status=0;std::memcpy(&status,buffer,sizeof(status));
        if(status==NET_SDK_CALLBACK_STATUS_FAILED && length>=2*sizeof(DWORD)) {
            DWORD error=0;std::memcpy(&error,static_cast<char*>(buffer)+sizeof(DWORD),sizeof(error));old_error=error;
        }
        old_status=status;
    } else if(type==NET_SDK_CALLBACK_TYPE_DATA) {
        if(length<sizeof(old_card)){old_error=1;return;}
        std::lock_guard<std::mutex> lock(old_mutex);
        std::memcpy(&old_card,buffer,sizeof(old_card));old_length=length;++old_count;
    }
}
static void legacy_read(LONG user,const std::string &number) {
    NET_DVR_CARD_CFG_COND cond{};cond.dwSize=sizeof(cond);cond.dwCardNum=1;cond.byCheckCardNo=1;
    LONG handle=NET_DVR_StartRemoteConfig(user,NET_DVR_GET_CARD_CFG,&cond,sizeof(cond),legacy_callback,nullptr);
    if(handle<0){permission_end(2,"SDK_ERROR",NET_DVR_GetLastError());return;}
    NET_DVR_CARD_CFG_SEND_DATA send{};send.dwSize=sizeof(send);std::memcpy(send.byCardNo,number.data(),number.size());
    if(!NET_DVR_SendRemoteConfig(handle,ENUM_ACS_SEND_DATA,reinterpret_cast<char*>(&send),sizeof(send)))old_error=NET_DVR_GetLastError();
    auto deadline=Clock::now()+std::chrono::seconds(10);
    while(Clock::now()<deadline && !old_error && (old_status==0 || old_status==NET_SDK_CALLBACK_STATUS_PROCESSING))
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    BOOL stopped=NET_DVR_StopRemoteConfig(handle);
    if(old_count==1) {
        std::string hex;const char *digits="0123456789abcdef";
        auto bytes=reinterpret_cast<const BYTE*>(&old_card.struValid);
        for(size_t i=0;i<sizeof(old_card.struValid);++i){hex+=digits[bytes[i]>>4];hex+=digits[bytes[i]&15];}
        emit("{\"kind\":\"legacyValidity\",\"command\":"+std::to_string(NET_DVR_GET_CARD_CFG)+
             ",\"nativeSize\":"+std::to_string(sizeof(old_card))+",\"declaredSize\":"+std::to_string(old_card.dwSize)+
             ",\"callbackLength\":"+std::to_string(old_length)+",\"card\":"+quote(text(old_card.byCardNo,ACS_CARD_NO_LEN))+
             ",\"validityHex\":"+quote(hex)+",\"start\":"+ex_time(old_card.struValid.struBeginTime)+
             ",\"expiry\":"+ex_time(old_card.struValid.struEndTime)+",\"type\":"+std::to_string(old_card.byCardType)+
             ",\"doorMask\":"+std::to_string(old_card.dwDoorRight)+"}");
    }
    permission_end(2,stopped && !old_error && old_status==NET_SDK_CALLBACK_STATUS_SUCCESS && old_count==1 ? "OK":"SDK_ERROR",old_error);
}
int main(int argc,char**argv) {
    abi();if(argc==2 && std::string(argv[1])=="--abi-only")return 0;
    rlimit core{0,0};setrlimit(RLIMIT_CORE,&core);
    std::string host,port,username,password,lib,number;
    for(auto*v:{&host,&port,&username,&password,&lib,&number})if(!std::getline(std::cin,*v))return 2;
    NET_DVR_LOCAL_SDK_PATH path{};NET_DVR_USER_LOGIN_INFO login{};
    if(!card_number(number) || host.size()>=sizeof(login.sDeviceAddress) || username.size()>=sizeof(login.sUserName) ||
       password.size()>=sizeof(login.sPassword) || lib.size()>=sizeof(path.sPath))return 2;
    std::memcpy(path.sPath,lib.data(),lib.size());
    std::string crypto=lib+"/libcrypto.so.1.1",ssl=lib+"/libssl.so.1.1";
    if(!stage("initialize",NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH,&path) &&
       NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH,crypto.data()) &&
       NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH,ssl.data()) && NET_DVR_Init()))return 2;
    NET_DVR_SetConnectTime(3000,1);NET_DVR_SetRecvTimeOut(3000);
    std::memcpy(login.sDeviceAddress,host.data(),host.size());std::memcpy(login.sUserName,username.data(),username.size());
    std::memcpy(login.sPassword,password.data(),password.size());login.wPort=static_cast<WORD>(std::stoul(port));
    NET_DVR_DEVICEINFO_V40 info{};LONG user=NET_DVR_Login_V40(&login,&info);
    std::fill(password.begin(),password.end(),'\0');std::memset(login.sPassword,0,sizeof(login.sPassword));
    if(!stage("login",user>=0)){stage("cleanup",NET_DVR_Cleanup());return 2;}
    NET_DVR_DEVICECFG_V40 device{};device.dwSize=sizeof(device);DWORD returned=0;
    if(NET_DVR_GetDVRConfig(user,NET_DVR_GET_DEVICECFG_V40,0,&device,sizeof(device),&returned) && returned==sizeof(device))
        emit("{\"kind\":\"device\",\"model\":"+quote(text(device.byDevTypeName,DEV_TYPE_NAME_LEN))+
             ",\"serial\":"+quote(text(info.struDeviceV30.sSerialNumber,SERIALNO_LEN))+
             ",\"firmware\":"+std::to_string(device.dwSoftwareVersion)+",\"build\":"+std::to_string(device.dwSoftwareBuildDate)+"}");
    card_request(user,1,number,nullptr,3000);
    legacy_read(user,number);
    bool logout=stage("logout",NET_DVR_Logout(user));bool cleanup=stage("cleanup",NET_DVR_Cleanup());
    return logout && cleanup ? 0:2;
}
