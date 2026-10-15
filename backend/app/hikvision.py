"""Isolated, read-only SDK process. Python never declares a vendor native ABI."""

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .controllers import ControllerReadUnavailable

LAGOS = ZoneInfo("Africa/Lagos")


def error_code(number):
    return {
        1: "AUTHENTICATION_FAILED",
        47: "AUTHENTICATION_FAILED",
        153: "ACCOUNT_LOCKED",
        2: "INSUFFICIENT_DEVICE_PERMISSION",
        13: "INSUFFICIENT_DEVICE_PERMISSION",
        7: "CONNECT_FAILED",
        8: "CONNECTION_LOST",
        9: "CONNECTION_LOST",
        10: "READ_TIMEOUT",
        14: "READ_TIMEOUT",
        23: "UNSUPPORTED",
        17: "INVALID_SDK_PARAMETER",
        156: "MISSING_CRYPTO_LIBRARY",
        157: "MISSING_SSL_LIBRARY",
        **{n: "MISSING_SDK_COMPONENT" for n in range(107, 116)},
        148: "MISSING_SSL_LIBRARY",
        1924: "SUBSCRIPTION_CAPACITY_EXHAUSTED",
    }.get(number, "SDK_OPERATION_FAILED")


def device_datetime(value, time_type=0):
    if time_type not in (0, 1) or not isinstance(value, list) or len(value) != 6:
        raise ValueError("Ambiguous device timestamp")
    return datetime(
        *value, tzinfo=LAGOS if time_type == 0 else timezone.utc
    ).astimezone(timezone.utc)


def parse_event(raw, identity, received_at=None, clock_verified=False):
    received_at = received_at or datetime.now(timezone.utc)
    required = ("major", "minor", "serial", "reader", "door", "timeType")
    if any(
        not isinstance(raw.get(k), int) or not 0 <= raw[k] <= 0xFFFFFFFF
        for k in required
    ):
        raise ValueError("Invalid SDK event fields")
    if raw.get("outcome") not in ("GRANTED", "DENIED", "UNKNOWN"):
        raise ValueError("Invalid SDK outcome")
    if (
        not isinstance(raw.get("inductive", 0), int)
        or not 0 <= raw.get("inductive", 0) <= 65535
    ):
        raise ValueError("Invalid SDK inductive type")
    credential = bytes.fromhex(raw["cardHex"])
    if len(credential) != 32:
        raise ValueError("Invalid SDK card buffer")
    flags = []
    try:
        card = credential.split(b"\0", 1)[0].decode("ascii")
        if any(ord(c) < 32 or ord(c) >= 127 for c in card):
            raise ValueError("Invalid card")
    except (UnicodeDecodeError, ValueError):
        card = ""
        flags.append("UNREADABLE_CARD")
    try:
        at = device_datetime(raw.get("time"), raw["timeType"])
        if at > received_at + timedelta(minutes=2):
            flags.append("FUTURE_DEVICE_TIME")
    except (ValueError, TypeError, OverflowError):
        at = None
        flags.append("INVALID_OR_AMBIGUOUS_TIME")
    if not clock_verified and raw["timeType"] == 0:
        flags.append("DEVICE_CLOCK_UNVERIFIED")
    if raw["serial"] == 0:
        flags.append("EVENT_ID_UNAVAILABLE")
    # Feed is deliberately excluded: live and historical representations dedup.
    # Include original date/time with serial to survive counter resets/reboots.
    identity_fields = [
        identity,
        raw["serial"],
        at.isoformat() if at else [raw.get("time"), raw["timeType"]],
        raw["major"],
        raw["minor"],
        raw["door"],
        raw["reader"],
        raw["cardHex"],
    ]
    key = (
        "hikvision:"
        + hashlib.sha256(
            json.dumps(identity_fields, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return {
        "source_key": key,
        "at": at,
        "card": card,
        "result": raw["outcome"],
        "device_door": str(raw["door"]),
        "device_reader": str(raw["reader"]),
        "type": "ACS_" + str(raw["major"]) + "_" + str(raw["minor"]),
        "flags": flags,
        "received_at": received_at,
        "raw": {
            "source": "HIKVISION",
            "major": raw["major"],
            "minor": raw["minor"],
            "serial": str(raw["serial"]),
            "time": raw.get("time"),
            "timeType": raw["timeType"],
            "inductive": raw.get("inductive", 0),
            "delivery": raw.get("feed", "UNKNOWN"),
            "cardHex": raw["cardHex"],
            "deviceDoor": str(raw["door"]),
            "deviceReader": str(raw["reader"]),
            "flags": flags,
        },
    }


class NativeSession:
    """One session shared by live callbacks and serialized history requests.

    Reader thread only receives bounded JSON frames. DB work is on the worker's
    normal thread. Events remain unacknowledged until the DB transaction commits.
    """

    def __init__(self, settings, *, permissions=False):
        self.settings = settings
        self.permissions = permissions
        self.permission_rows = []
        self.permission_end = None
        self.process = None
        self.reader_thread = None
        self.messages = queue.Queue(maxsize=4096)
        self.live = deque()
        self.metadata = {"state": "BLOCKED", "code": "NOT_CONNECTED", "stages": {}}
        self.ready = False
        self.auth_failed = False
        self.next_attempt = 0.0
        self.backoff = 5.0
        self.last_message = time.monotonic()
        self.failure = None
        self.sequence = 0
        self.batch = []
        self.batch_end = None
        self.identity = ""
        self.reconnected = False
        self.lock = threading.RLock()

    def _reader(self, process, messages):
        try:
            while True:
                line = process.stdout.readline(131073)
                if not line:
                    messages.put_nowait(
                        {"kind": "fault", "code": "NATIVE_PROCESS_EXITED"}
                    )
                    return
                if len(line) > 131072:
                    messages.put_nowait(
                        {"kind": "fault", "code": "NATIVE_FRAME_TOO_LARGE"}
                    )
                    process.terminate()
                    return
                if line.startswith(b"IFT "):
                    messages.put_nowait(json.loads(line[4:]))
        except (queue.Full, ValueError, OSError):
            # Never silently overwrite/drop a callback. Force recovery from the
            # last durable history checkpoint; caller exposes the gap as a fault.
            if self.process is process:
                self.failure = "NATIVE_QUEUE_OR_PROTOCOL_FAILURE"
            try:
                process.terminate()
            except OSError:
                pass

    def _handle(self, message):
        self.last_message = time.monotonic()
        kind = message.get("kind")
        if kind == "fault":
            self.failure = (
                message.get("code")
                if message.get("code")
                in {
                    "CALLBACK_GAP",
                    "CONNECTION_LOST",
                    "NATIVE_PROCESS_EXITED",
                    "NATIVE_FRAME_TOO_LARGE",
                }
                else "NATIVE_PROTOCOL_FAILURE"
            )
        elif kind == "status":
            stage = message["stage"]
            if stage not in {
                "initialize",
                "login",
                "device",
                "deviceTime",
                "capabilities",
                "subscription",
                "logout",
                "cleanup",
                "unsubscribe",
            }:
                return
            status = message["status"]
            code = "OK" if status == "VERIFIED" else error_code(message.get("error"))
            self.metadata["stages"][stage] = {
                "status": status,
                "code": code,
                "errorNumber": message.get("error", 0),
            }
            if stage == "initialize" and status != "VERIFIED":
                self.failure = code
            if stage == "login" and status != "VERIFIED":
                self.failure = code
                self.auth_failed = code in {"AUTHENTICATION_FAILED", "ACCOUNT_LOCKED"}
        elif kind == "identity":
            if not message["serial"]:
                raise ValueError("Device identity unavailable")
            self.identity = hashlib.sha256(message["serial"].encode()).hexdigest()
        elif kind == "device":
            self.metadata["model"] = str(message["model"])[:64]
            self.metadata["firmwareRaw"] = int(message["firmwareRaw"])
            self.metadata["buildDateRaw"] = int(message["buildDateRaw"])
            version, build = self.metadata["firmwareRaw"], self.metadata["buildDateRaw"]
            self.metadata["firmwarePacked"] = (
                f"V{version >> 24}.{(version >> 16) & 255}.{version & 65535}"
            )
            try:
                date = datetime(2000 + (build >> 16), (build >> 8) & 255, build & 255)
                self.metadata["firmwareBuild"] = date.strftime("%y%m%d")
            except ValueError:
                self.metadata["firmwareBuild"] = "UNKNOWN"
        elif kind == "subscriptionMode":
            self.metadata["subscriptionMode"] = (
                "REALTIME" if message.get("deployType") == 1 else "CLIENT"
            )
        elif kind == "sdk":
            self.metadata["sdkVersionRaw"] = int(message["version"])
        elif kind == "permissionAbi":
            self.metadata["permissionAbi"] = {
                key: int(message[key])
                for key in [
                    "cardV50",
                    "cardCondition",
                    "cardQuery",
                    "week",
                    "holiday",
                    "group",
                    "template",
                ]
            }
        elif kind == "deviceTime":
            try:
                at = device_datetime(message["time"])
                skew = (at - datetime.now(timezone.utc)).total_seconds()
                self.metadata.update(
                    deviceTime=at.isoformat(),
                    clockSkewSeconds=round(skew),
                    clockVerified=abs(skew) <= 120,
                )
            except (ValueError, TypeError):
                self.metadata["clockVerified"] = False
        elif kind == "capabilities":
            xml = message["xml"]
            if len(xml) > 65536 or "<!" in xml:
                self.metadata["stages"]["capabilities"] = {
                    "status": "FAILED",
                    "code": "INVALID_CAPABILITY_XML",
                }
                return
            root = ElementTree.fromstring(xml)
            allow = {
                "isSupportGetDeviceEvent",
                "isSupportGetDeviceEventByTime",
                "doorNo",
                "cardReaderNo",
            }
            self.metadata["capabilities"] = {
                node.tag.split("}")[-1]: {
                    "value": (node.text or "").strip()[:64],
                    "options": node.attrib.get("opt", "")[:128],
                }
                for node in root.iter()
                if node.tag.split("}")[-1] in allow
            }
            # Retain the card/schedule subtree with its hierarchy and attributes;
            # options are advertised limits, never proof of a successful write.
            self.metadata["permissionCapabilities"] = {
                node.tag.split("}")[-1]: ElementTree.tostring(node, encoding="unicode")
                for node in root
                if node.tag.split("}")[-1] in {"Card", "CardRightPlan"}
            }
            self.metadata["stages"]["capabilities"] = {
                "status": "VERIFIED",
                "code": "OK",
            }
        elif kind == "ready":
            self.ready = True
            self.metadata.update(
                state="CONNECTED",
                code="OK",
                lastCommunication=datetime.now(timezone.utc).isoformat(),
            )
        elif kind == "event":
            if not self.identity:
                raise ValueError("Device identity unavailable")
            if message.get("feed") not in {"LIVE", "HISTORY"}:
                raise ValueError("Invalid event feed")
            event = parse_event(
                message,
                self.identity,
                clock_verified=self.metadata.get("clockVerified", False),
            )
            self.metadata["lastCommunication"] = datetime.now(timezone.utc).isoformat()
            if message.get("feed") == "LIVE":
                self.metadata["liveReceived"] = self.metadata.get("liveReceived", 0) + 1
                self.metadata["lastLiveEventKey"] = event["source_key"]
                self.metadata["lastLiveReceivedAt"] = datetime.now(
                    timezone.utc
                ).isoformat()
                if len(self.live) >= 4096:
                    self.failure = "APPLICATION_QUEUE_OVERFLOW"
                else:
                    self.live.append(event)
            elif (
                message.get("feed") == "HISTORY"
                and message.get("request") == self.sequence
            ):
                self.metadata["historyReceived"] = (
                    self.metadata.get("historyReceived", 0) + 1
                )
                self.batch.append(event)
        elif kind == "historyEnd" and message.get("request") == self.sequence:
            self.batch_end = message
            if message.get("complete"):
                self.metadata["lastCommunication"] = datetime.now(
                    timezone.utc
                ).isoformat()
        elif kind == "permissionRow" and message.get("request") == self.sequence:
            if len(self.permission_rows) >= 10000:
                self.failure = "PERMISSION_INVENTORY_OVERFLOW"
            else:
                self.permission_rows.append(message["data"])
        elif kind == "permissionEnd" and message.get("request") == self.sequence:
            self.permission_end = message

    def pump(self, wait=0):
        try:
            message = (
                self.messages.get(timeout=wait) if wait else self.messages.get_nowait()
            )
            self._handle(message)
        except queue.Empty:
            pass
        except (ValueError, TypeError, KeyError, ElementTree.ParseError):
            self.failure = "NATIVE_EVENT_PARSE_FAILED"
        if self.failure:
            self.metadata.update(state="FAILED", code=self.failure)
            raise ControllerReadUnavailable(self.failure)

    def start(self):
        with self.lock:
            if self.ready and self.process and self.process.poll() is None:
                return
            if self.auth_failed:
                raise ControllerReadUnavailable("AUTHENTICATION_STOPPED")
            if time.monotonic() < self.next_attempt:
                raise ControllerReadUnavailable("RECONNECT_WAIT")
            s = self.settings
            values = [
                s.hikvision_host,
                str(s.hikvision_port),
                s.hikvision_username.get_secret_value(),
                s.hikvision_password.get_secret_value(),
                str(s.hikvision_sdk_dir / "lib"),
                str(int(s.hikvision_connect_timeout * 1000)),
            ]
            if not values[2] or not values[3]:
                raise ControllerReadUnavailable("CREDENTIALS_MISSING")
            if any("\n" in v or "\r" in v or "\0" in v for v in values):
                raise ControllerReadUnavailable("INVALID_LOCAL_CONFIGURATION")
            if not s.hikvision_bridge_path.is_file():
                raise ControllerReadUnavailable("NATIVE_BRIDGE_MISSING")
            if not (s.hikvision_sdk_dir / "lib/libhcnetsdk.so").is_file():
                raise ControllerReadUnavailable("SDK_PACKAGE_MISSING")
            self.close()
            self.failure = None
            self.ready = False
            self.messages = queue.Queue(maxsize=4096)
            self.metadata = {"state": "CONNECTING", "code": "CONNECTING", "stages": {}}
            try:
                self.process = subprocess.Popen(
                    [str(s.hikvision_bridge_path)]
                    + (["--permissions"] if self.permissions else []),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=s.hikvision_bridge_path.parent,
                    env={
                        "PATH": os.defpath,
                        "LANG": "C",
                        "LD_LIBRARY_PATH": str(s.hikvision_sdk_dir / "lib")
                        + ":"
                        + str(s.hikvision_sdk_dir / "lib/HCNetSDKCom"),
                    },
                )
                self.reader_thread = threading.Thread(
                    target=self._reader, args=(self.process, self.messages), daemon=True
                )
                self.reader_thread.start()
                self.process.stdin.write(("\n".join(values) + "\n").encode())
                self.process.stdin.flush()
                deadline = time.monotonic() + s.hikvision_process_timeout
                while not self.ready and time.monotonic() < deadline:
                    self.pump(0.1)
                if not self.ready:
                    raise ControllerReadUnavailable("NATIVE_TIMEOUT")
                self.reconnected = True
                self.backoff = 5
            except (OSError, ControllerReadUnavailable) as exc:
                code = (
                    str(exc)
                    if isinstance(exc, ControllerReadUnavailable)
                    else "NATIVE_LIBRARY_OR_EXECUTION_FAILED"
                )
                if self.process and self.process.poll() == 127:
                    code = "MISSING_NATIVE_LIBRARY"
                self.close()
                self.metadata.update(state="FAILED", code=code)
                self.next_attempt = time.monotonic() + self.backoff
                self.backoff = min(300, self.backoff * 2)
                raise ControllerReadUnavailable(code) from None

    def read_history(self, start, end, limit):
        with self.lock:
            self.start()
            self.sequence += 1
            self.batch = []
            self.batch_end = None
            fields = ["HISTORY", str(self.sequence), str(limit)]
            for value in (start, end):
                fields.extend(
                    value.astimezone(LAGOS).strftime("%Y %m %d %H %M %S").split()
                )
            try:
                self.process.stdin.write((" ".join(fields) + "\n").encode())
                self.process.stdin.flush()
                deadline = time.monotonic() + self.settings.hikvision_process_timeout
                while self.batch_end is None and time.monotonic() < deadline:
                    self.pump(0.1)
                if self.batch_end is None:
                    raise ControllerReadUnavailable("NATIVE_TIMEOUT")
                if self.batch_end.get("error"):
                    code = error_code(self.batch_end["error"])
                    self.metadata["history"] = {
                        "status": "UNSUPPORTED" if code == "UNSUPPORTED" else "FAILED",
                        "code": code,
                    }
                else:
                    self.metadata["history"] = {
                        "status": "VERIFIED"
                        if self.batch_end["complete"]
                        else "PARTIAL",
                        "code": "OK"
                        if self.batch_end["complete"]
                        else "HISTORY_INCOMPLETE",
                    }
                return self.batch, bool(self.batch_end["complete"])
            except (OSError, ControllerReadUnavailable):
                self.close()
                self.next_attempt = time.monotonic() + self.backoff
                self.backoff = min(300, self.backoff * 2)
                raise ControllerReadUnavailable("HISTORY_READ_FAILED") from None

    def poll_live(self):
        with self.lock:
            self.start()
            while not self.messages.empty():
                self.pump()
            if (
                time.monotonic() - self.last_message
                > self.settings.hikvision_process_timeout
            ):
                self.close()
                raise ControllerReadUnavailable("NATIVE_HEARTBEAT_TIMEOUT")
            return list(self.live)

    def permission_request(self, name, fields=()):
        if not self.permissions or name not in {
            "CARD_READ",
            "PLAN_READ",
            "AUTHORIZE",
            "CARD_WRITE",
            "PLAN_WRITE",
            "MANAGE",
        }:
            raise ControllerReadUnavailable("PERMISSION_CHANNEL_REQUIRED")
        if (
            name in {"AUTHORIZE", "CARD_WRITE", "PLAN_WRITE"}
            or (name == "MANAGE" and fields and fields[0] == "APPLY")
        ) and self.settings.controller_mode != "HIKVISION_CONTROLLED":
            raise ControllerReadUnavailable("READ_ONLY")
        tokens = [str(v) for v in fields]
        if any(not v or any(c.isspace() or c == "\0" for c in v) for v in tokens):
            raise ControllerReadUnavailable("INVALID_PERMISSION_INPUT")
        with self.lock:
            self.start()
            self.sequence += 1
            self.permission_rows, self.permission_end = [], None
            try:
                self.process.stdin.write(
                    (" ".join([name, str(self.sequence), *tokens]) + "\n").encode()
                )
                self.process.stdin.flush()
                deadline = time.monotonic() + self.settings.hikvision_process_timeout
                while self.permission_end is None and time.monotonic() < deadline:
                    self.pump(0.1)
                if self.permission_end is None:
                    raise ControllerReadUnavailable("PERMISSION_TIMEOUT")
                return self.permission_rows, self.permission_end
            except (OSError, ControllerReadUnavailable):
                self.close()
                raise ControllerReadUnavailable("PERMISSION_UNCERTAIN") from None

    def acknowledge_live(self, count):
        for _ in range(count):
            self.live.popleft()

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        process = self.process
        self.ready = False
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.stdin.write(b"QUIT\n")
                process.stdin.flush()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        # Process exit can precede reader delivery of the final callback frames.
        if self.reader_thread:
            self.reader_thread.join(timeout=1)
        reader_stopped = not self.reader_thread or not self.reader_thread.is_alive()
        # Capture logout/cleanup results, without treating intentional EOF as a
        # new connection failure or discarding live events already in transit.
        while not self.messages.empty():
            message = self.messages.get_nowait()
            if message.get("kind") not in {"fault", "ready", "heartbeat"}:
                try:
                    self._handle(message)
                except (ValueError, TypeError, KeyError, ElementTree.ParseError):
                    self.failure = "NATIVE_EVENT_PARSE_FAILED"
        process.stdin.close()
        if reader_stopped:
            process.stdout.close()
        self.metadata.update(
            state="BLOCKED" if reader_stopped else "FAILED",
            code="SESSION_CLOSED"
            if reader_stopped
            else "NATIVE_READER_SHUTDOWN_FAILED",
        )
