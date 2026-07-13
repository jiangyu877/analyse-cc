import time

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import text

from app.extensions import db
from app.security.authorization import permission_required

custom_query_bp = Blueprint("custom_query", __name__)

BLOCKED_TOKENS = (
    " INSERT ", " UPDATE ", " DELETE ", " DROP ", " ALTER ", " CREATE ",
    " TRUNCATE ", " GRANT ", " REVOKE ", " COPY ", " CALL ", " DO ",
)


@custom_query_bp.get("/sql-lab")
@permission_required("sql.execute")
def custom_query_page():
    return render_template("custom_query.html")


@custom_query_bp.post("/sql-lab/execute")
@permission_required("sql.execute")
def execute_custom():
    sql = (request.get_json(silent=True) or {}).get("sql", "").strip().rstrip(";")
    normalized = f" {sql.upper()} "
    if not sql or not normalized.lstrip().startswith(("SELECT ", "WITH ", "EXPLAIN ")):
        return jsonify(success=False, message="SQL 实验页仅允许 SELECT、WITH 或 EXPLAIN")
    if any(token in normalized for token in BLOCKED_TOKENS) or ";" in sql:
        return jsonify(success=False, message="检测到禁止的写操作或多语句")

    limit = current_app.config["SQL_QUERY_MAX_ROWS"]
    timeout = current_app.config["SQL_QUERY_TIMEOUT_MS"]
    started = time.perf_counter()
    try:
        with db.session.begin():
            db.session.execute(text("SET TRANSACTION READ ONLY"))
            db.session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": str(timeout)})
            result = db.session.execute(text(f"SELECT * FROM ({sql}) AS readonly_query LIMIT {limit + 1}"))
            rows = result.mappings().all()
        truncated = len(rows) > limit
        rows = rows[:limit]
        columns = list(rows[0].keys()) if rows else list(result.keys())
        return jsonify(
            success=True, columns=columns,
            rows=[[str(row[col]) if row[col] is not None else "" for col in columns] for row in rows],
            row_count=len(rows), truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:
        db.session.rollback()
        return jsonify(success=False, message=f"查询失败：{exc}"), 400
