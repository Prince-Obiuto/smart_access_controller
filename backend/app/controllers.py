from dataclasses import dataclass
from typing import Protocol
from datetime import datetime
from .models import Operation, Controller

CONTROLLED_WRITE_MESSAGE = (
    "Controller writes require an active controlled authorization scope."
)

class ControllerReadUnavailable(Exception):
    """Safe transport failure; never substitute simulated events or move a checkpoint."""


class HikvisionAdapter:
    """SDK read session used by the collector; controlled writes use the write bridge."""

    mode = "HIKVISION_CONTROLLED"

    def __init__(self, settings=None):
        self.settings = settings
        if settings is not None:
            from .hikvision import NativeSession

            self.session = NativeSession(settings)
        else:
            self.session = None

    def execute(self, operation, controller, controls):
        return AdapterResult("PENDING", CONTROLLED_WRITE_MESSAGE)

    def collect_events(self, controller, start, end):
        if self.session is None:
            raise ControllerReadUnavailable("SDK_TRANSPORT_UNAVAILABLE")
        if controller.id != self.settings.hikvision_controller_id:
            raise ControllerReadUnavailable("CONTROLLER_TARGET_NOT_CONFIGURED")
        return self.session.read_history(
            start, end, self.settings.hikvision_event_limit
        )


def adapter_for(settings):
    if settings.controller_mode == "HIKVISION_CONTROLLED":
        return HikvisionAdapter(settings)
    if settings.controller_mode == "SIMULATED":
        return SimulatedAdapter()
    # Never turn an unknown mode into mock success.
    raise ValueError("Unsupported controller mode.")


@dataclass
class AdapterResult:
    status: str
    message: str
    confirmed_state: str | None = None


class AccessControllerAdapter(Protocol):
    mode: str

    def execute(
        self, operation: Operation, controller: Controller, controls: dict
    ) -> AdapterResult: ...
    def collect_events(
        self, controller: Controller, start: datetime, end: datetime
    ) -> list[dict] | tuple[list[dict], bool]: ...


class SimulatedAdapter:
    mode = "SIMULATED"

    def execute(self, operation, controller, controls):
        if not controller.online or controls.get("offline"):
            return AdapterResult("FAILED", "Simulated controller is disconnected.")
        if operation.target_type == "DOOR":
            if controls.get("unlockTimeout"):
                return AdapterResult(
                    "UNCERTAIN",
                    "Simulated unlock timed out. Physical state UNKNOWN; do not repeat automatically.",
                )
            return AdapterResult(
                "CONFIRMED",
                "Simulated 5-second command acknowledged; physical door state UNKNOWN.",
                "TIMED_RELEASE",
            )
        if operation.desired == "DISABLED" and controls.get("failDisable"):
            return AdapterResult(
                "FAILED", "Revoked locally — controller disable not confirmed."
            )
        if operation.desired != "DISABLED" and controls.get("delayActivation"):
            return AdapterResult("PENDING", "Simulated activation/update is delayed.")
        return AdapterResult(
            "CONFIRMED",
            "Simulated controller confirmation received.",
            operation.desired,
        )

    def collect_events(self, controller, start, end):
        if not controller.online:
            return []
        # Stable time buckets simulate a live/backfill overlap. Real collection is
        # an independent future adapter, never invented Hikvision HTTP endpoints.
        bucket = int(end.timestamp()) // 120
        return [
            {
                "source_key": f"simulated:{controller.id}:{bucket}",
                "at": end,
                "card": "0000000000",
                "direction": "ENTRY",
                "type": "UNKNOWN_CARD",
            }
        ]
