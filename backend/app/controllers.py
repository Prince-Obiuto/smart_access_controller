from dataclasses import dataclass
from typing import Protocol
from datetime import datetime
from .models import Operation, Controller


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
    ) -> list[dict]: ...


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
