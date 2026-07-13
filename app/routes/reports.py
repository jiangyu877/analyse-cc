import csv
import io
import json
from datetime import date

from flask import Blueprint, Response, abort, render_template, request, stream_with_context
from sqlalchemy import text

from app.extensions import db
from app.security.authorization import permission_required
from app.utils import audit


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

REPORT_MAX_DAYS = 366
REPORT_DEFAULT_DAYS = 30

REPORT_DEFINITIONS = {
    "daily": {
        "label": "日销售报表",
        "columns": (
            {"key": "snapshot_date", "label": "快照日期", "kind": "date"},
            {"key": "sales_date", "label": "销售日期", "kind": "date"},
            {"key": "order_count", "label": "订单数", "kind": "number"},
            {"key": "item_quantity", "label": "商品件数", "kind": "number"},
            {"key": "gross_amount", "label": "销售总额", "kind": "money"},
            {"key": "net_amount", "label": "净销售额", "kind": "money"},
        ),
        "sql": """
            SELECT snapshot_date, sales_date, order_count, item_quantity,
                   gross_amount, net_amount
            FROM ads.daily_sales
            WHERE snapshot_date BETWEEN :start AND :end
            ORDER BY snapshot_date DESC, sales_date DESC
        """,
    },
    "product": {
        "label": "商品报表",
        "columns": (
            {"key": "snapshot_date", "label": "快照日期", "kind": "date"},
            {"key": "product_id", "label": "商品 ID", "kind": "identifier"},
            {"key": "category_id", "label": "分类 ID", "kind": "identifier"},
            {"key": "quantity", "label": "销售件数", "kind": "number"},
            {"key": "gross_amount", "label": "销售总额", "kind": "money"},
            {"key": "net_amount", "label": "净销售额", "kind": "money"},
        ),
        "sql": """
            SELECT snapshot_date, product_id, category_id, quantity,
                   gross_amount, net_amount
            FROM ads.product_sales
            WHERE snapshot_date BETWEEN :start AND :end
            ORDER BY snapshot_date DESC, net_amount DESC, product_id
        """,
    },
    "category": {
        "label": "分类报表",
        "columns": (
            {"key": "snapshot_date", "label": "快照日期", "kind": "date"},
            {"key": "category_id", "label": "分类 ID", "kind": "identifier"},
            {"key": "quantity", "label": "销售件数", "kind": "number"},
            {"key": "gross_amount", "label": "销售总额", "kind": "money"},
            {"key": "net_amount", "label": "净销售额", "kind": "money"},
        ),
        "sql": """
            SELECT snapshot_date, category_id, quantity, gross_amount, net_amount
            FROM ads.category_sales
            WHERE snapshot_date BETWEEN :start AND :end
            ORDER BY snapshot_date DESC, net_amount DESC, category_id NULLS LAST
        """,
    },
    "customer": {
        "label": "客户报表",
        "columns": (
            {"key": "snapshot_date", "label": "快照日期", "kind": "date"},
            {"key": "customer_id", "label": "客户 ID", "kind": "identifier"},
            {"key": "frequency", "label": "消费频次", "kind": "number"},
            {"key": "monetary", "label": "累计净消费", "kind": "money"},
            {"key": "recency_days", "label": "最近消费间隔（天）", "kind": "number"},
        ),
        "sql": """
            SELECT snapshot_date, customer_id, frequency, monetary, recency_days
            FROM ads.customer_profile
            WHERE snapshot_date BETWEEN :start AND :end
            ORDER BY snapshot_date DESC, monetary DESC, customer_id
        """,
    },
}


def _parse_date(raw_value, field_name, default):
    value = (raw_value or "").strip()
    if not value:
        return default
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        abort(400, description=f"{field_name} must be an ISO date")


def _parse_filters():
    dimension = (request.args.get("dimension") or "daily").strip().lower()
    if dimension not in REPORT_DEFINITIONS:
        abort(400, description="unsupported report dimension")

    end = _parse_date(request.args.get("end"), "end", date.today())
    default_start = date.fromordinal(
        max(date.min.toordinal(), end.toordinal() - REPORT_DEFAULT_DAYS + 1)
    )
    start = _parse_date(request.args.get("start"), "start", default_start)
    if start > end:
        abort(400, description="start must not be after end")
    if (end - start).days + 1 > REPORT_MAX_DAYS:
        abort(400, description=f"date range cannot exceed {REPORT_MAX_DAYS} days")
    return {"dimension": dimension, "start": start, "end": end}


def _report_data(filters):
    definition = REPORT_DEFINITIONS[filters["dimension"]]
    rows = db.session.execute(
        text(definition["sql"]),
        {"start": filters["start"], "end": filters["end"]},
    ).mappings().all()
    return definition, rows


def _csv_stream(definition, rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")

    yield "\ufeff"
    writer.writerow([column["key"] for column in definition["columns"]])
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        writer.writerow([
            "" if row[column["key"]] is None else row[column["key"]]
            for column in definition["columns"]
        ])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


@reports_bp.get("")
@permission_required("analysis.read")
def index():
    filters = _parse_filters()
    definition, rows = _report_data(filters)
    return render_template(
        "reports/index.html",
        dimensions=REPORT_DEFINITIONS,
        filters=filters,
        report=definition,
        rows=rows,
    )


@reports_bp.get("/export.csv")
@permission_required("analysis.export")
def export_csv():
    filters = _parse_filters()
    definition, rows = _report_data(filters)
    audit(
        "report.export",
        "analysis_report",
        details=json.dumps({
            "dimension": filters["dimension"],
            "start": filters["start"].isoformat(),
            "end": filters["end"].isoformat(),
            "row_count": len(rows),
        }),
    )
    db.session.commit()

    filename = (
        f"report-{filters['dimension']}-{filters['start'].isoformat()}-"
        f"{filters['end'].isoformat()}.csv"
    )
    return Response(
        stream_with_context(_csv_stream(definition, rows)),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
