import re
from datetime import datetime, timezone
from typing import Annotated, Literal, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    StringConstraints,
    model_validator,
)

RoleName = Literal["SUPER_ADMIN", "ADMIN", "MANAGER", "MANAGEMENT", "USER"]
PersonKind = Literal["STUDENT", "STAFF", "VISITOR"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True
    )


def email(value: str, university: bool = False):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(
        r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}",
        value,
    ):
        raise ValueError("Enter a valid email address.")
    if university and value.split("@")[1] != "futo.edu.ng":
        raise ValueError("Use an email at exactly futo.edu.ng.")
    return value


def timestamp(value: str | datetime):
    if isinstance(value, str):
        if len(value) == 10:
            value += "T00:00:00+01:00"
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timestamp must include its timezone.")
    return value.astimezone(timezone.utc)


class Registration(StrictModel):
    type: PersonKind
    reference: str = Field(default="", max_length=100)
    firstName: str = Field(min_length=1, max_length=100)
    lastName: str = Field(min_length=1, max_length=100)
    email: str
    phone: str = Field(min_length=1, max_length=40)
    department: str = Field(min_length=1, max_length=200)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=12, max_length=128
    )

    @model_validator(mode="after")
    def valid_identity(self):
        self.email = email(self.email, self.type != "VISITOR")
        if self.type != "VISITOR" and not self.reference:
            raise ValueError("Registration number or staff ID is required.")
        return self


class Login(StrictModel):
    email: str
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=1, max_length=128
    )


class PersonData(StrictModel):
    type: Literal["STUDENT", "STAFF"]
    reference: str = Field(min_length=1, max_length=100)
    firstName: str = Field(min_length=1, max_length=100)
    lastName: str = Field(min_length=1, max_length=100)
    email: str
    phone: str = Field(min_length=1, max_length=40)
    department: str = Field(min_length=1, max_length=200)
    status: Literal["ACTIVE", "DISABLED"]
    start: datetime | None = None
    expiry: datetime | None = None

    @field_validator("start", "expiry", mode="before")
    @classmethod
    def dates(cls, v):
        return timestamp(v) if v else None

    @model_validator(mode="after")
    def valid_identity(self):
        self.email = email(self.email, True)
        if (self.start or self.expiry) and (
            not self.start or not self.expiry or self.expiry <= self.start
        ):
            raise ValueError("Access expiry must follow start.")
        return self


class ExceptionData(StrictModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    closed: bool
    opens: str | None = None
    closes: str | None = None
    label: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_exception(self):
        datetime.fromisoformat(self.date)
        if not self.closed and (
            not self.opens
            or not self.closes
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.opens)
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.closes)
            or self.opens >= self.closes
        ):
            raise ValueError("Special opening hours must be valid and ordered.")
        return self


class ScheduleData(StrictModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    opens: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    exceptions: list[ExceptionData] = Field(default_factory=list, max_length=366)
    sync: str | None = None  # Frontend compatibility only; ignored when applying.

    @model_validator(mode="after")
    def valid_schedule(self):
        if (
            self.opens >= self.closes
            or any(d < 0 or d > 6 for d in self.weekdays)
            or len(set(self.weekdays)) != len(self.weekdays)
            or len({e.date for e in self.exceptions}) != len(self.exceptions)
        ):
            raise ValueError("Working days, hours and dates must be valid and unique.")
        return self


class PaymentSettings(StrictModel):
    payment: str = Field(min_length=1, max_length=2000)
    uploadLimit: int = Field(ge=1024, le=20 * 1024 * 1024)
    privilegedCardId: str | None = (
        None  # Server retains its own designation; client cannot set it here.
    )


class Controls(StrictModel):
    offline: bool = False
    failDisable: bool = False
    delayActivation: bool = False
    unlockTimeout: bool = False


class Filters(StrictModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, populate_by_name=True
    )
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    person: str | None = None
    reference: str | None = None
    card: str | None = None
    controller: str | None = None
    door: str | None = None
    direction: Literal["ENTRY", "EXIT"] | None = None
    result: str | None = None
    type: str | None = None
    actor: str | None = None
    action: str | None = None
    target: str | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)

    @field_validator("from_", "to")
    @classmethod
    def dates(cls, v):
        if v:
            timestamp(v)
        return v

    @model_validator(mode="after")
    def range(self):
        if self.from_ and self.to and timestamp(self.to) < timestamp(self.from_):
            raise ValueError("Through date must not precede from date.")
        return self


def command(command_name, **fields):
    return create_model(
        command_name.replace("-", "_").title() + "Command",
        __base__=StrictModel,
        type=(Literal[command_name], command_name),
        **fields,
    )


RequiredStr = Annotated[str, Field(min_length=1, max_length=200)]
CommandTypes = [
    command("person", id=(str | None, None), data=(PersonData, ...)),
    command("dates", personId=(str, ...), start=(str, ...), expiry=(str, ...)),
    command("application", kind=(Literal["FIRST", "REPLACEMENT"], ...)),
    command(
        "review",
        id=(str, ...),
        approve=(bool, ...),
        reason=(Annotated[str, Field(max_length=2000)] | None, None),
    ),
    command(
        "issue",
        id=(str, ...),
        number=(Annotated[str, Field(pattern=r"^\d{4,32}$")], ...),
    ),
    command(
        "revoke",
        assignmentId=(str, ...),
        reason=(Literal["MISSING", "DAMAGED", "NOT_PERMITTED"], ...),
        own=(bool, False),
    ),
    command(
        "retry-sync",
        assignmentId=(str | None, None),
        personId=(str | None, None),
        schedule=(bool, False),
    ),
    command(
        "visit",
        purpose=(RequiredStr, ...),
        host=(RequiredStr, ...),
        start=(str, ...),
        expiry=(str, ...),
    ),
    command("visitor-approve", id=(str, ...), note=(RequiredStr, ...)),
    command("visitor-request", personId=(str, ...), purpose=(RequiredStr, ...),
            host=(RequiredStr, ...), start=(str, ...), expiry=(str, ...)),
    command("visitor-assign", id=(str, ...), cardId=(str, ...)),
    command(
        "visitor-return",
        id=(str, ...),
        returned=(bool, ...),
        condition=(Literal["GOOD", "MISSING", "DAMAGED"], ...),
    ),
    command("schedule", schedule=(ScheduleData, ...)),
    command("unlock", doorId=(str, ...), confirmed=(bool, ...)),
    command("settings", settings=(PaymentSettings, ...)),
    command("role", personId=(str, ...), role=(RoleName, ...)),
    command("designate", cardId=(str, ...)),
    command("device", id=(str, ...), name=(RequiredStr, ...)),
    command(
        "restore", family=(Literal["people", "events", "audits"], ...), id=(str, ...)
    ),
    command("notice-read", id=(str, ...)),
    command("demo-controls", controls=(Controls, ...)),
    command(
        "demo-event",
        event=(
            Literal[
                "ENTRY",
                "EXIT",
                "UNKNOWN",
                "REVOKED",
                "FORCED_OPEN",
                "HELD_OPEN",
                "READER_FAULT",
            ],
            ...,
        ),
    ),
    command("pool-card", number=(Annotated[str, Field(pattern=r"^\d{4,32}$")], ...)),
]
CommandUnion = Annotated[Union[tuple(CommandTypes)], Field(discriminator="type")]
