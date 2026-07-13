from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.security.authorization import permission_required
from app.services.algorithms import AlgorithmError, run_churn, run_kmeans, run_rfm

algorithms_bp = Blueprint("algorithms", __name__, url_prefix="/algorithms")

TASK_LABELS = {
    "rfm": "RFM 客户价值",
    "kmeans": "客户智能分群",
    "churn": "客户流失预警",
}

SEGMENT_ADVICE = {
    "高价值客户": "重点维护，优先提供会员权益",
    "新近客户": "及时回访，引导完成下一次购买",
    "重要保持客户": "保持稳定触达，推荐关联商品",
    "流失预警客户": "尽快唤回，提供有时效的优惠",
    "一般客户": "常规运营，观察消费变化",
}


def _number(value):
    return float(value or 0)


def _percent(value, total):
    return round(value * 100 / total, 1) if total else 0


def _cluster_profile(row, baseline):
    recent = _number(row["avg_recency"]) <= baseline["recency"]
    frequent = _number(row["avg_frequency"]) >= baseline["frequency"]
    valuable = _number(row["avg_monetary"]) >= baseline["monetary"]

    if recent and frequent and valuable:
        return "核心活跃群", "保持会员权益，优先推荐新品"
    if recent and (frequent or valuable):
        return "近期潜力群", "趁活跃期促进复购和客单提升"
    if not recent and valuable:
        return "高价值待唤醒群", "安排专属回访和定向优惠"
    if not recent and not frequent:
        return "沉睡客户群", "使用低成本渠道分批唤回"
    return "稳定维护群", "保持常规触达，观察消费变化"


def _summary_item(label, value, hint, icon):
    return {"label": label, "value": value, "hint": hint, "icon": icon}


def _rfm_result(task_id):
    records = db.session.execute(text("""
        SELECT segment, COUNT(*)::int AS customer_count,
               AVG(recency_days)::float AS avg_recency,
               AVG(frequency)::float AS avg_frequency,
               AVG(monetary)::float AS avg_monetary
        FROM ml.rfm_result
        WHERE task_id = :task_id
        GROUP BY segment
        ORDER BY customer_count DESC, segment
    """), {"task_id": task_id}).mappings().all()
    rows = [dict(row) for row in records]
    total = sum(row["customer_count"] for row in rows)
    for row in rows:
        row["percentage"] = _percent(row["customer_count"], total)
        row["advice"] = SEGMENT_ADVICE.get(row["segment"], "持续观察客户消费变化")

    largest = rows[0]["segment"] if rows else "暂无"
    avg_recency = sum(_number(row["avg_recency"]) * row["customer_count"] for row in rows) / total if total else 0
    avg_monetary = sum(_number(row["avg_monetary"]) * row["customer_count"] for row in rows) / total if total else 0
    return {
        "type": "rfm",
        "title": "客户价值分层结果",
        "subtitle": "按最近消费、购买次数和消费金额，将客户分成便于运营的五类。",
        "rows": rows,
        "summary": [
            _summary_item("分析客户", f"{total:,}", "本次纳入计算的客户", "ti-users"),
            _summary_item("最大客群", largest, "当前人数最多的分层", "ti-user-star"),
            _summary_item("平均未消费", f"{avg_recency:.0f} 天", "距最近一次消费", "ti-calendar-time"),
            _summary_item("人均累计消费", f"¥{avg_monetary:,.0f}", "基于现有交易记录", "ti-currency-yuan"),
        ],
        "chart": {
            "type": "doughnut",
            "labels": [row["segment"] for row in rows],
            "values": [row["customer_count"] for row in rows],
            "label": "客户数",
        },
    }


def _kmeans_result(task_id, metrics):
    records = db.session.execute(text("""
        SELECT cr.cluster_label, COUNT(*)::int AS customer_count,
               AVG(r.recency_days)::float AS avg_recency,
               AVG(r.frequency)::float AS avg_frequency,
               AVG(r.monetary)::float AS avg_monetary
        FROM ml.cluster_result cr
        JOIN ml.model_task task ON task.task_id = cr.task_id
        JOIN ml.rfm_result r ON r.customer_id = cr.customer_id
          AND r.task_id = COALESCE(
              NULLIF(task.parameters->>'rfm_task_id', '')::bigint,
              (
                  SELECT source.task_id
                  FROM ml.model_task source
                  WHERE source.task_type = 'rfm' AND source.status = 'success'
                    AND source.finished_at <= task.started_at
                  ORDER BY source.finished_at DESC, source.task_id DESC
                  LIMIT 1
              )
          )
        WHERE cr.task_id = :task_id
        GROUP BY cr.cluster_label
        ORDER BY cr.cluster_label
    """), {"task_id": task_id}).mappings().all()
    rows = [dict(row) for row in records]
    total = sum(row["customer_count"] for row in rows)
    baseline = {
        "recency": sum(_number(row["avg_recency"]) * row["customer_count"] for row in rows) / total if total else 0,
        "frequency": sum(_number(row["avg_frequency"]) * row["customer_count"] for row in rows) / total if total else 0,
        "monetary": sum(_number(row["avg_monetary"]) * row["customer_count"] for row in rows) / total if total else 0,
    }
    for row in rows:
        row["percentage"] = _percent(row["customer_count"], total)
        row["name"], row["advice"] = _cluster_profile(row, baseline)
        row["display_label"] = f"客户群 {row['cluster_label'] + 1} · {row['name']}"

    largest = max(rows, key=lambda row: row["customer_count"], default=None)
    silhouette = _number(metrics.get("silhouette:all"))
    quality = "分群清晰" if silhouette >= 0.5 else "分群可用" if silhouette >= 0.25 else "边界较模糊"
    return {
        "type": "kmeans",
        "title": "客户智能分群结果",
        "subtitle": "系统按照客户消费习惯自动分组，并给出每组最值得采取的运营动作。",
        "rows": rows,
        "summary": [
            _summary_item("分析客户", f"{total:,}", "本次参与分群的客户", "ti-users"),
            _summary_item("客户群数", f"{len(rows)} 组", "系统识别出的差异群体", "ti-chart-dots"),
            _summary_item("最大客群", largest["name"] if largest else "暂无", "当前人数最多的群体", "ti-users-group"),
            _summary_item("结果质量", quality, f"清晰度 {silhouette:.2f}", "ti-shield-check"),
        ],
        "chart": {
            "type": "bar",
            "labels": [row["display_label"] for row in rows],
            "values": [row["customer_count"] for row in rows],
            "label": "客户数",
        },
    }


def _churn_result(task_id, metrics):
    records = db.session.execute(text("""
        WITH classified AS (
            SELECT CASE WHEN churn_probability >= 0.7 THEN '高风险'
                        WHEN churn_probability >= 0.3 THEN '需关注'
                        ELSE '低风险' END AS risk_level,
                   churn_probability
            FROM ml.churn_prediction
            WHERE task_id = :task_id
        )
        SELECT risk_level, COUNT(*)::int AS customer_count,
               AVG(churn_probability)::float AS avg_probability
        FROM classified
        GROUP BY risk_level
        ORDER BY CASE risk_level WHEN '高风险' THEN 1 WHEN '需关注' THEN 2 ELSE 3 END
    """), {"task_id": task_id}).mappings().all()
    rows = [dict(row) for row in records]
    total = sum(row["customer_count"] for row in rows)
    for row in rows:
        row["percentage"] = _percent(row["customer_count"], total)

    high_risk = next((row["customer_count"] for row in rows if row["risk_level"] == "高风险"), 0)
    avg_probability = sum(_number(row["avg_probability"]) * row["customer_count"] for row in rows) / total if total else 0
    top_customers = db.session.execute(text("""
        SELECT c.customer_id, c.customer_no, c.name,
               p.churn_probability::float AS churn_probability,
               r.recency_days, r.frequency, r.monetary::float AS monetary
        FROM ml.churn_prediction p
        JOIN ml.model_task task ON task.task_id = p.task_id
        JOIN biz.customer c ON c.customer_id = p.customer_id
        LEFT JOIN ml.rfm_result r ON r.customer_id = p.customer_id
          AND r.task_id = COALESCE(
              NULLIF(task.parameters->>'rfm_task_id', '')::bigint,
              (
                  SELECT source.task_id
                  FROM ml.model_task source
                  WHERE source.task_type = 'rfm' AND source.status = 'success'
                    AND source.finished_at <= task.started_at
                  ORDER BY source.finished_at DESC, source.task_id DESC
                  LIMIT 1
              )
          )
        WHERE p.task_id = :task_id
        ORDER BY p.churn_probability DESC, c.customer_id
        LIMIT 10
    """), {"task_id": task_id}).mappings().all()
    auc = _number(metrics.get("auc:training"))
    return {
        "type": "churn",
        "title": "客户流失风险结果",
        "subtitle": "风险越高，客户近期停止购买的可能性越大，应优先安排回访。",
        "rows": rows,
        "top_customers": [dict(row) for row in top_customers],
        "summary": [
            _summary_item("预测客户", f"{total:,}", "本次评估的客户", "ti-users"),
            _summary_item("高风险客户", f"{high_risk:,}", "建议优先联系", "ti-user-exclamation"),
            _summary_item("平均流失风险", f"{avg_probability * 100:.1f}%", "全体客户平均概率", "ti-percentage"),
            _summary_item("模型识别力", f"{auc * 100:.1f}%", "数值越高，区分能力越好", "ti-target-arrow"),
        ],
        "chart": {
            "type": "doughnut",
            "labels": [row["risk_level"] for row in rows],
            "values": [row["customer_count"] for row in rows],
            "label": "客户数",
            "colors": ["#d1495b", "#e6a23c", "#13795b"],
        },
    }


def _load_result(task):
    if not task or task["status"] != "success":
        return None
    metrics = dict(task["metrics"] or {})
    loaders = {
        "rfm": lambda: _rfm_result(task["task_id"]),
        "kmeans": lambda: _kmeans_result(task["task_id"], metrics),
        "churn": lambda: _churn_result(task["task_id"], metrics),
    }
    loader = loaders.get(task["task_type"])
    return loader() if loader else None


@algorithms_bp.get("")
@permission_required("model.read")
def index():
    tasks = db.session.execute(text("""
        SELECT t.task_id, t.task_type, t.status, t.parameters, t.started_at,
               t.finished_at, t.error_message,
               COALESCE(jsonb_object_agg(m.metric_name || ':' || m.dataset, m.metric_value)
                   FILTER (WHERE m.metric_id IS NOT NULL), '{}'::jsonb) AS metrics
        FROM ml.model_task t
        LEFT JOIN ml.model_metric m ON m.task_id = t.task_id
        GROUP BY t.task_id ORDER BY t.started_at DESC LIMIT 100
    """)).mappings().all()
    selected_id = request.args.get("task_id", type=int)
    selected_task = next((task for task in tasks if task["task_id"] == selected_id), None)
    if selected_task is None:
        selected_task = next((task for task in tasks if task["status"] == "success"), tasks[0] if tasks else None)

    return render_template(
        "algorithms.html",
        tasks=tasks,
        selected_task=selected_task,
        result=_load_result(selected_task),
        task_labels=TASK_LABELS,
        gradio_public_url=current_app.config["GRADIO_PUBLIC_URL"],
    )


@algorithms_bp.post("/run/<task_type>")
@permission_required("model.run")
def run(task_type):
    try:
        if task_type == "rfm":
            task_id = run_rfm(session["user_id"])
        elif task_type == "kmeans":
            task_id = run_kmeans(session["user_id"], request.form.get("clusters", 4))
        elif task_type == "churn":
            task_id = run_churn(session["user_id"], request.form.get("observation_days", 90))
        else:
            raise AlgorithmError("未知算法类型")
        flash(f"算法任务 {task_id} 已完成，结果已生成", "success")
        return redirect(url_for("algorithms.index", task_id=task_id, _anchor="task-result"))
    except (AlgorithmError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("algorithms.index"))
