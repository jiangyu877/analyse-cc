from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from app.routes import budgets_bp
from app.db import query, execute
from datetime import datetime


@budgets_bp.route('/budgets')
def budgets_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('budgets.html')


@budgets_bp.route('/budgets/data')
def get_budgets():
    if 'user_id' not in session:
        return jsonify(success=False)
    user_id = session['user_id']

    # Get current month budgets with actual spending
    budgets = query("""
        SELECT
            b.id,
            COALESCE(sc.category_name, '未分类') as category_name,
            COALESCE(sc.icon, '📦') as icon,
            b.budget_month::TEXT,
            b.budget_amount,
            COALESCE(SUM(s.amount), 0) as spent_amount,
            CASE
                WHEN b.budget_amount > 0
                THEN ROUND((COALESCE(SUM(s.amount), 0) / b.budget_amount * 100)::numeric, 1)
                ELSE 0
            END as usage_pct
        FROM budgets b
        LEFT JOIN spending_category sc ON b.category_id = sc.category_id
        LEFT JOIN spending_record s ON s.user_id = b.user_id
            AND s.category_id = b.category_id
            AND DATE_TRUNC('month', s.spend_date) = DATE_TRUNC('month', b.budget_month::DATE)
        WHERE b.user_id = %s
        GROUP BY b.id, sc.category_name, sc.icon, b.budget_month, b.budget_amount
        ORDER BY b.budget_month DESC, b.id
    """, (user_id,))

    # Get available categories
    categories = query("""
        SELECT category_id, category_name, parent_category
        FROM spending_category
        ORDER BY sort_order, category_id
    """)

    return jsonify(
        success=True,
        budgets=[dict(r) for r in budgets],
        categories=[dict(r) for r in categories]
    )


@budgets_bp.route('/budgets/add', methods=['POST'])
def add_budget():
    if 'user_id' not in session:
        return jsonify(success=False, message='请先登录')
    user_id = session['user_id']
    data = request.json

    try:
        category_id = int(data['category_id'])
        budget_amount = float(data['budget_amount'])
        budget_month = data.get('budget_month', datetime.now().strftime('%Y-%m-01'))

        if budget_amount <= 0:
            return jsonify(success=False, message='预算金额必须大于0')

        execute("""
            INSERT INTO budgets (user_id, category_id, budget_month, budget_amount)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, category_id, budget_month)
            DO UPDATE SET budget_amount = EXCLUDED.budget_amount, updated_at = NOW()
        """, (user_id, category_id, budget_month, budget_amount))

        _log_action(user_id, '新增预算', f'分类ID:{category_id}, 月份:{budget_month}, 金额:{budget_amount}')
        return jsonify(success=True, message='预算设置成功')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@budgets_bp.route('/budgets/delete', methods=['POST'])
def delete_budget():
    if 'user_id' not in session:
        return jsonify(success=False, message='请先登录')
    data = request.json
    try:
        execute("DELETE FROM budgets WHERE id = %s AND user_id = %s",
                (int(data['id']), session['user_id']))
        _log_action(session['user_id'], '删除预算', f'预算ID:{data["id"]}')
        return jsonify(success=True, message='预算已删除')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@budgets_bp.route('/budgets/summary')
def budget_summary():
    if 'user_id' not in session:
        return jsonify(success=False)
    user_id = session['user_id']

    # Current month budget overview
    summary = query("""
        SELECT
            COALESCE(SUM(b.budget_amount), 0) as total_budget,
            COALESCE(SUM(s.spent), 0) as total_spent,
            COUNT(b.id) as budget_count
        FROM (
            SELECT b.id, b.budget_amount
            FROM budgets b
            WHERE b.user_id = %s
                AND DATE_TRUNC('month', b.budget_month::DATE)
                    = DATE_TRUNC('month', CURRENT_DATE)
        ) b
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(s2.amount), 0) as spent
            FROM spending_record s2
            WHERE s2.user_id = %s
                AND s2.category_id IN (
                    SELECT b2.category_id FROM budgets b2
                    WHERE b2.id = b.id
                )
                AND DATE_TRUNC('month', s2.spend_date)
                    = DATE_TRUNC('month', CURRENT_DATE)
        ) s ON true
    """, (user_id, user_id))

    if summary:
        total_budget = float(summary[0]['total_budget'])
        total_spent = float(summary[0]['total_spent'])
        usage_pct = round((total_spent / total_budget * 100), 1) if total_budget > 0 else 0
        return jsonify(success=True, total_budget=total_budget,
                       total_spent=total_spent, usage_pct=usage_pct,
                       budget_count=summary[0]['budget_count'])
    return jsonify(success=True, total_budget=0, total_spent=0, usage_pct=0, budget_count=0)


def _log_action(user_id, action, details):
    try:
        from app.db import execute as db_exec
        db_exec(
            "INSERT INTO system_logs (user_id, action, details) VALUES (%s, %s, %s)",
            (user_id, action, details)
        )
    except Exception:
        pass