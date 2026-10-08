import csv
import io
import re
from datetime import timedelta
from sqlalchemy import select, func
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from .models import AccessEvent, Audit
from .schemas import timestamp
from .security import require
from .common import audit
from .views import event_view, audit_view


def query_records(family, filters):
    model = AccessEvent if family == "events" else Audit
    q = select(model).where(model.deleted_at.is_(None))
    values = filters.model_dump(by_alias=True, exclude_none=True)
    for key, value in values.items():
        if not value or key in ("page", "pageSize"):
            continue
        if key == "from":
            q = q.where(model.at >= timestamp(value))
            continue
        if key == "to":
            q = (
                q.where(model.at < timestamp(value) + timedelta(days=1))
                if len(value) == 10
                else q.where(model.at <= timestamp(value))
            )
            continue
        column = getattr(model, "event_type" if key == "type" else key, None)
        if column is None:
            continue
        q = (
            q.where(column == value)
            if key in ("direction", "result", "controller", "door", "type")
            else q.where(
                column.ilike(
                    "%"
                    + str(value)
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    + "%",
                    escape="\\",
                )
            )
        )
    return model, q.order_by(model.at.desc(), model.id)


def list_records(db, actor, settings, family, filters):
    require(db, actor, "logs", settings)
    model, q = query_records(family, filters)
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    rows = list(
        db.scalars(
            q.offset((filters.page - 1) * filters.pageSize).limit(filters.pageSize)
        )
    )
    serialize = event_view if family == "events" else audit_view
    return {
        "items": [serialize(r) for r in rows],
        "total": total,
        "page": filters.page,
        "pageSize": filters.pageSize,
    }


def safe_csv(value):
    value = str(value or "")
    return "'" + value if re.match(r"^\s*[=+\-@\t\r]", value) else value


def export_records(db, actor, settings, family, filters, format):
    require(db, actor, "export", settings)
    model, q = query_records(family, filters)
    rows = list(db.scalars(q))
    serialize = event_view if family == "events" else audit_view
    keys = (
        [
            "id",
            "at",
            "person",
            "reference",
            "card",
            "assignmentId",
            "controller",
            "door",
            "direction",
            "result",
            "type",
            "receivedAt",
            "deviceReader",
            "deviceDoor",
            "timestampFlags",
            "simulated",
        ]
        if family == "events"
        else ["id", "at", "actor", "action", "target", "result"]
    )
    records = [serialize(row) for row in rows]
    if format == "csv":
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(keys)
        for row in records:
            writer.writerow([safe_csv(row.get(k, "")) for k in keys])
        content = ("\ufeff" + out.getvalue()).encode()
        mime = "text/csv;charset=utf-8"
    else:
        out = io.BytesIO()
        pdf = Canvas(out, pagesize=A4)
        y = 810
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(35, y, "IFT E-Library: filtered records (source labelled per event)")
        y -= 25
        pdf.setFont("Helvetica", 8)
        for row in records:
            for key in keys:
                value = str(row.get(key, ""))
                lines = [value[i : i + 100] for i in range(0, max(len(value), 1), 100)]
                for i, line in enumerate(lines):
                    if y < 40:
                        pdf.showPage()
                        pdf.setFont("Helvetica", 8)
                        y = 810
                    pdf.drawString(35, y, (key + ": " if i == 0 else "") + line)
                    y -= 11
            y -= 12
        pdf.save()
        content = out.getvalue()
        mime = "application/pdf"
    audit(
        db, actor, "EXPORT_" + format.upper(), f"{family}: {len(rows)} filtered records"
    )
    return content, mime
