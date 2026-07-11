from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from app.routes import analysis_bp
from app.db import query


@analysis_bp.route('/analysis')
def analysis_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('analysis.html')


@analysis_bp.route('/analysis/data', methods=['POST'])
def analysis_data():
    if 'user_id' not in session:
        return jsonify(success=False)
    user_id = session['user_id']
    view_type = request.json.get('view_type', 'trend')

    if view_type == 'trend':
        rows = query("""
            SELECT TO_CHAR(DATE_TRUNC('month', spend_date)::DATE, 'YYYY-MM') as label,
                   SUM(amount) as value
            FROM spending_record WHERE user_id = %s
            GROUP BY DATE_TRUNC('month', spend_date) ORDER BY label
        """, (user_id,))
        return jsonify(success=True, labels=[r['label'] for r in rows],
                       values=[float(r['value']) for r in rows])

    elif view_type == 'category':
        rows = query("""
            SELECT COALESCE(sc.parent_category, '其他') as label,
                   SUM(s.amount) as value
            FROM spending_record s
            LEFT JOIN spending_category sc ON s.category_id = sc.category_id
            WHERE s.user_id = %s
            GROUP BY sc.parent_category ORDER BY value DESC
        """, (user_id,))
        return jsonify(success=True, labels=[r['label'] for r in rows],
                       values=[float(r['value']) for r in rows])

    elif view_type == 'merchant':
        rows = query("""
            SELECT COALESCE(m.merchant_name, '其他') as label,
                   SUM(s.amount) as value
            FROM spending_record s
            LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
            WHERE s.user_id = %s
            GROUP BY m.merchant_name ORDER BY value DESC LIMIT 10
        """, (user_id,))
        return jsonify(success=True, labels=[r['label'] for r in rows],
                       values=[float(r['value']) for r in rows])

    elif view_type == 'region':
        rows = query("""
            SELECT COALESCE(cu.province, '其他') as label,
                   SUM(s.amount) as value
            FROM spending_record s
            LEFT JOIN consumer_unit cu ON s.cu_id = cu.cu_id
            WHERE s.user_id = %s
            GROUP BY cu.province ORDER BY value DESC
        """, (user_id,))
        return jsonify(success=True, labels=[r['label'] for r in rows],
                       values=[float(r['value']) for r in rows])

    return jsonify(success=False)
