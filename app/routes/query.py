import csv
import io
from datetime import datetime

from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, Response
from app.routes import query_bp
from app.db import query


@query_bp.route('/query')
def query_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('query.html')


@query_bp.route('/query/dropdowns')
def get_dropdowns():
    """Return filter dropdown data"""
    merchants = query("SELECT merchant_id, merchant_name FROM merchant ORDER BY merchant_name")
    categories = query("SELECT category_id, category_name FROM spending_category WHERE parent_category IS NULL ORDER BY category_name")
    provinces = query("SELECT DISTINCT province FROM consumer_unit WHERE province IS NOT NULL ORDER BY province")
    methods = query("SELECT DISTINCT payment_method FROM spending_record WHERE payment_method IS NOT NULL ORDER BY payment_method")
    return jsonify(
        merchants=[dict(r) for r in merchants],
        categories=[dict(r) for r in categories],
        provinces=[r['province'] for r in provinces],
        payment_methods=[r['payment_method'] for r in methods]
    )


@query_bp.route('/query/search', methods=['POST'])
def search_query():
    if 'user_id' not in session:
        return jsonify(success=False)
    user_id = session['user_id']
    data = request.json

    conditions = ["s.user_id = %s"]
    params = [user_id]

    if data.get('start_date'):
        conditions.append("s.spend_date >= %s")
        params.append(data['start_date'])
    if data.get('end_date'):
        conditions.append("s.spend_date <= %s")
        params.append(data['end_date'])
    if data.get('merchant_id'):
        conditions.append("s.merchant_id = %s")
        params.append(int(data['merchant_id']))
    if data.get('category_id'):
        conditions.append("s.category_id = %s")
        params.append(int(data['category_id']))
    if data.get('province'):
        conditions.append("cu.province = %s")
        params.append(data['province'])
    if data.get('payment_method'):
        conditions.append("s.payment_method = %s")
        params.append(data['payment_method'])
    if data.get('min_amount'):
        conditions.append("s.amount >= %s")
        params.append(float(data['min_amount']))
    if data.get('max_amount'):
        conditions.append("s.amount <= %s")
        params.append(float(data['max_amount']))

    where = " AND ".join(conditions)

    rows = query(f"""
        SELECT s.record_id, s.spend_date::TEXT, s.amount, s.payment_method,
               COALESCE(m.merchant_name, '') as merchant_name,
               COALESCE(sc.category_name, '') as category_name,
               COALESCE(cu.province || ' ' || cu.city, '') as region,
               s.remarks
        FROM spending_record s
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        LEFT JOIN consumer_unit cu ON s.cu_id = cu.cu_id
        WHERE {where}
        ORDER BY s.spend_date DESC
        LIMIT 500
    """, params)

    total = sum(float(r['amount']) for r in rows) if rows else 0

    return jsonify(success=True, records=[dict(r) for r in rows],
                   total=round(total, 2), count=len(rows))


@query_bp.route('/query/export', methods=['POST'])
def export_query():
    if 'user_id' not in session:
        return jsonify(success=False)
    data = request.json

    conditions = ["s.user_id = %s"]
    params = [session['user_id']]

    if data.get('start_date'):
        conditions.append("s.spend_date >= %s")
        params.append(data['start_date'])
    if data.get('end_date'):
        conditions.append("s.spend_date <= %s")
        params.append(data['end_date'])

    where = " AND ".join(conditions)
    rows = query(f"""
        SELECT s.spend_date::TEXT, s.amount, s.payment_method,
               COALESCE(m.merchant_name, '') as merchant_name,
               COALESCE(sc.category_name, '') as category_name
        FROM spending_record s
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        WHERE {where}
        ORDER BY s.spend_date DESC
    """, params)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['消费日期', '金额', '支付方式', '商户', '分类'])
    for r in rows:
        writer.writerow([r['spend_date'], r['amount'], r['payment_method'], r['merchant_name'], r['category_name']])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=消费记录_{datetime.now().strftime("%Y%m%d")}.csv',
                 'Content-Type': 'text/csv; charset=utf-8-sig'}
    )


@query_bp.route('/query/quick-chart', methods=['POST'])
def quick_chart():
    if 'user_id' not in session:
        return jsonify(success=False)
    user_id = session['user_id']
    group_by = request.json.get('group_by', 'merchant')

    if group_by == 'merchant':
        rows = query("""
            SELECT COALESCE(m.merchant_name, '其他') as label, SUM(s.amount) as value
            FROM spending_record s LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
            WHERE s.user_id = %s GROUP BY m.merchant_name ORDER BY value DESC LIMIT 10
        """, (user_id,))
    elif group_by == 'category':
        rows = query("""
            SELECT COALESCE(sc.category_name, '其他') as label, SUM(s.amount) as value
            FROM spending_record s LEFT JOIN spending_category sc ON s.category_id = sc.category_id
            WHERE s.user_id = %s GROUP BY sc.category_name ORDER BY value DESC LIMIT 10
        """, (user_id,))
    elif group_by == 'region':
        rows = query("""
            SELECT COALESCE(cu.province, '其他') as label, SUM(s.amount) as value
            FROM spending_record s LEFT JOIN consumer_unit cu ON s.cu_id = cu.cu_id
            WHERE s.user_id = %s GROUP BY cu.province ORDER BY value DESC
        """, (user_id,))
    else:
        return jsonify(success=False)

    return jsonify(success=True, labels=[r['label'] for r in rows],
                   values=[float(r['value']) for r in rows])
