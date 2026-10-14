"""Read-only enrolment, normal visitor approvals and narrow firmware compatibility."""
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.common import now, iso, set_setting
from app.enrolment import start_real, poll_real, confirm_real
from app.hikvision_existing import compatible_match, expected_validity, validate_designation, DEVICE, ExistingTemplateTransport
from app.hikvision_writes import card_matches
from app.models import Account, Person, AccessEvent, Visit, Operation, Card, Enrolment, Assignment, Application
from app.security import DomainError
from conftest import session_client, cmd, write, approved
from app.hikvision_writes import WriteBlocked


def validity_example():
    intent = {"card": "00009999", "enabled": True, "start": [2026,10,13,17,15,0], "expiry": [2026,10,13,17,25,0]}
    record = {"card": intent["card"], "valid":1,"type":1,"leader":0,"doors":[1]+[0]*255,
              "plans":[[3,0,0,0]]+[[0]*4 for _ in range(255)],"validityEnabled":1,"beginFlag":0,"endFlag":0,
              "timeType":0,"start":intent["start"],"expiry":intent["expiry"]}
    profile = {"ownerTimedEvidenceVerified":True,"device":DEVICE,
               "abi":{"validitySize":52,"v50Size":2708,"v50ValidityOffset":300,"timeExSize":8}}
    data = bytearray(expected_validity(intent)); data[1:3]=bytes(2)
    raw = {"card":intent["card"],"command":2178,"direction":"READ","nativeSize":2708,"declaredSize":2708,
           "callbackLength":2708,"validityOffset":300,"validitySize":52,"hex":data.hex()}
    return record,intent,profile,raw


def test_compatibility_does_not_weaken_global_readback():
    record,intent,profile,raw=validity_example()
    assert not card_matches(record,intent,3)
    assert compatible_match(record,intent,3,profile,raw)
    profile["device"]={**DEVICE,"build":0}
    assert not compatible_match(record,intent,3,profile,raw)


@pytest.mark.parametrize("field", ["card","doors","plans","start","expiry","leader","validityEnabled","type"])
def test_compatibility_retains_all_permission_comparisons(field):
    record,intent,profile,raw=validity_example()
    record[field] = {"card":"99998888","doors":[1,1]+[0]*254,"plans":[[1,0,0,0]]+[[0]*4 for _ in range(255)],
                     "start":[2026,10,13,17,14,0],"expiry":[2026,10,13,17,26,0],"leader":1,"validityEnabled":0,"type":6}[field]
    assert not compatible_match(record,intent,3,profile,raw)


def test_truncated_or_changed_reserved_native_bytes_are_not_accepted():
    record,intent,profile,raw=validity_example()
    for change in [{"callbackLength":2707},{"validityOffset":48},{"hex":raw["hex"][:-2]+"01"}]:
        assert not compatible_match(record,intent,3,profile,{**raw,**change})


def make_event(at, number="00009999", simulated=False):
    return AccessEvent(source_key=str(at)+number+str(simulated), at=at, received_at=at+timedelta(seconds=1),
        person="Unknown credential",reference="",card=number,controller="controller-1",direction="ENTRY",result="DENIED",
        event_type="ACS_5_9",simulated=simulated,raw={"source":"HIKVISION","major":5,"deviceDoor":"1",
        "deviceReader":"1","readerMappingVerified":True,"flags":[],"delivery":"LIVE"})


def test_reader_capture_is_fresh_bounded_distinct_and_preserves_string(env):
    settings,database,accounts,_=env
    settings.controller_mode="HIKVISION_READ_ONLY"
    with database.session() as db:
        set_setting(db,"hikvision:controller-1",{"state":"CONNECTED","lastCommunication":iso(now())})
        scan=start_real(db,db.get(Account,accounts["ADMIN"][0]),settings)
        start = now()
        db.add(make_event(start-timedelta(minutes=1)))
        db.add(make_event(start+timedelta(seconds=1),simulated=True));db.flush()
        assert poll_real(db,scan).state=="WAITING"
        db.add(make_event(start+timedelta(seconds=2)));db.flush()
        assert poll_real(db,scan).number=="00009999"
        confirm_real(db,scan)
        assert db.scalar(select(Operation)) is None
        db.add(make_event(start+timedelta(seconds=3),"00008888"));db.flush()
        assert poll_real(db,scan).state=="AMBIGUOUS"
        with pytest.raises(DomainError):confirm_real(db,scan)


def test_reader_capture_refuses_stale_controller_and_overlapping_session(env):
    settings,database,accounts,_=env
    with database.session() as db:
        actor=db.get(Account,accounts["ADMIN"][0])
        with pytest.raises(DomainError):start_real(db,actor,settings)
        set_setting(db,"hikvision:controller-1",{"state":"CONNECTED","lastCommunication":iso(now())})
        start_real(db,actor,settings)
        with pytest.raises(DomainError):start_real(db,actor,settings)


def test_admin_request_is_not_approval_and_cannot_impersonate_nonvisitor(env):
    _,database,accounts,_=env
    with database.session() as db:
        db.get(Person,accounts["USER"][1]).kind="VISITOR";db.commit()
    body={"type":"visitor-request","personId":accounts["USER"][1],"host":"Test host","purpose":"Acceptance",
          "start":iso(now()),"expiry":iso(now()+timedelta(minutes=10))}
    cmd(session_client(env,"MANAGER"),body,403)
    cmd(session_client(env,"ADMIN"),body)
    with database.session() as db:
        visit=db.scalar(select(Visit))
        assert visit.state=="REQUESTED" and not visit.approval and not visit.assignment_id
        assert db.scalar(select(Operation)) is None
    cmd(session_client(env,"ADMIN"),{**body,"personId":accounts["ADMIN"][1]},403)


def test_existing_scope_never_automatically_adopts_backlog(env):
    from app.service import AccessService
    settings,database,accounts,_=env
    settings.controller_mode="HIKVISION_CONTROLLED"
    with database.session() as db:
        set_setting(db,"hikvision:write-scope:controller-1",{"enabled":True,"epoch":"test","kind":"EXISTING_TEMPLATE"})
        AccessService(db,db.get(Account,accounts["ADMIN"][0]),settings).queue("SCHEDULE","normal","SYNCHRONIZED",1)
        assert "writeEpoch" not in db.scalar(select(Operation)).data


def test_scope_rejects_existing_rights_and_admin_identity(env):
    _,database,accounts,_=env
    with database.session() as db:
        cards={"00009999":{"kind":"PERSONAL","personIds":[accounts["USER"][1]]}}
        validate_designation(db,cards,{"cards":[]})
        record,_,_,_=validity_example()
        with pytest.raises(WriteBlocked,match="EXISTING_CONTROLLER_PERMISSION"):
            validate_designation(db,cards,{"cards":[record]})
        cards["00009999"]["personIds"]=[accounts["SUPER_ADMIN"][1]]
        with pytest.raises(WriteBlocked,match="ACTIVE_TEST_USER_REQUIRED"):
            validate_designation(db,cards,{"cards":[]})


def test_scope_rejects_local_revocation_and_wrong_card_kind(env):
    _,database,accounts,_=env
    with database.session() as db:
        card=Card(number="00009999",kind="PERSONAL",revoked=True,disabled=True)
        db.add(card);db.flush()
        cards={card.number:{"kind":"PERSONAL","personIds":[accounts["USER"][1]]}}
        with pytest.raises(WriteBlocked,match="LOCAL_CONFLICT"):
            validate_designation(db,cards,{"cards":[]})
        card.revoked=False;card.kind="VISITOR"
        with pytest.raises(WriteBlocked,match="LOCAL_CONFLICT"):
            validate_designation(db,cards,{"cards":[]})


def test_native_failure_evidence_keeps_actual_bytes_without_claiming_confirmation(monkeypatch):
    saved=[]
    monkeypatch.setattr("app.hikvision_existing.save",lambda name,data: saved.append(data) or "private-artifact.json")
    transport=ExistingTemplateTransport(None,{})
    transport.rows=[{"kind":"permissionEnd","code":"SDK_ERROR","error":17}]
    result=transport.failure_evidence("CARD_WRITE_FAILED_OR_UNCERTAIN")
    assert result=={"failureArtifact":"private-artifact.json"}
    assert saved[0]["native"][0]["error"]==17
    assert saved[0]["readbackVerified"] is False


def test_real_reader_http_session_persists_and_confirms_without_issuance(env):
    settings,database,_,_=env
    settings.controller_mode="HIKVISION_READ_ONLY"
    admin=session_client(env,"ADMIN")
    with database.session() as db:
        set_setting(db,"hikvision:controller-1",{"state":"CONNECTED","lastCommunication":iso(now())})
        db.commit()
    response=write(admin,"/api/enrolment-sessions",{})
    assert response.status_code==200
    scan=response.json()
    assert scan["state"]=="WAITING"
    assert admin.get('/api/enrolment-sessions/'+scan['id']).json()['state']=='WAITING'
    with database.session() as db:
        db.add(make_event(now()));db.commit()
    assert admin.get('/api/enrolment-sessions/'+scan['id']).json()['number']=='00009999'
    response=write(admin,'/api/enrolment-sessions/'+scan['id']+'/confirm',{})
    assert response.status_code==200 and response.json()=='00009999'
    assert write(admin,'/api/enrolment-sessions/'+scan['id']+'/cancel',{}).status_code==200
    with database.session() as db:
        assert db.get(Enrolment,scan['id']).state=='CONFIRMED'
        assert db.scalar(select(Card)) is None
        assert db.scalar(select(Operation)) is None


def test_scope_accepts_only_owned_approved_pending_personal_issuance(env,png):
    _,_,admin,application_id=approved(env,png)
    settings,database,accounts,_=env
    settings.controller_mode="HIKVISION_READ_ONLY"
    assert cmd(admin,{'type':'issue','id':application_id,'number':'00009999'})['result']=='PENDING'
    with database.session() as db:
        pending=db.scalar(select(Assignment))
        cards={'00009999':{'kind':'PERSONAL','personIds':[accounts['USER'][1]]}}
        validate_designation(db,cards,{'cards':[]})
        record,_,_,_=validity_example()
        with pytest.raises(WriteBlocked,match='EXISTING_CONTROLLER_PERMISSION'):
            validate_designation(db,cards,{'cards':[record]})
        pending.state='ACTIVE'
        with pytest.raises(WriteBlocked,match='ALREADY_ASSIGNED'):
            validate_designation(db,cards,{'cards':[]})
        pending.state='PENDING'
        db.get(Application,application_id).evidence_id=None
        with pytest.raises(WriteBlocked,match='ALREADY_ASSIGNED'):
            validate_designation(db,cards,{'cards':[]})
