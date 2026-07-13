from flask import Blueprint, render_template
from sqlalchemy import text

from app.extensions import db
from app.security.authorization import permission_required

imports_bp = Blueprint("imports", __name__, url_prefix="/imports")


@imports_bp.get("")
@permission_required("import.read")
def index():
    summary = db.session.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM biz.customer) AS customer_count,
          (SELECT COUNT(*) FROM biz.sales_order) AS order_count,
          (SELECT COUNT(*) FROM dwd.consumption_flow WHERE flow_type = 'payment') AS consumption_count,
          (SELECT COALESCE(SUM(net_amount), 0) FROM dwd.consumption_flow) AS net_amount
    """)).mappings().one()
    batches = db.session.execute(text("""
        SELECT batch_no, source_name, status, customer_count, transaction_count,
               started_at, finished_at, error_message
        FROM ods.import_batch ORDER BY started_at DESC LIMIT 50
    """)).mappings().all()
    return render_template("imports.html", summary=summary, batches=batches)
