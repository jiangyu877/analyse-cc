# -*- coding: utf-8 -*-
"""
消费数据分析预测工作台 - Gradio 版
作者：张跃星
"""

import gradio as gr
import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from sqlalchemy.engine import make_url
from datetime import datetime, timedelta
from app.config import Config
import warnings
warnings.filterwarnings('ignore')

# ========== 数据库连接 ==========
def get_conn():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    options = {
        "host": url.host,
        "port": url.port or 5432,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
        "cursor_factory": psycopg2.extras.RealDictCursor,
    }
    options.update(dict(url.query))
    return psycopg2.connect(**options)


# ========== 分析 Tab：消费趋势 ==========
def analysis_trend(user_id="1"):
    """月度消费趋势图"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT TO_CHAR(DATE_TRUNC('month', spend_date)::DATE, 'YYYY-MM') AS month,
               SUM(amount) AS total
        FROM spending_record
        WHERE user_id = %s
        GROUP BY DATE_TRUNC('month', spend_date)
        ORDER BY month
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None, "暂无数据"

    df = pd.DataFrame(rows)
    df['total'] = df['total'].astype(float)

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.fill_between(range(len(df)), df['total'], alpha=0.2, color='#00a8ff')
    ax.plot(range(len(df)), df['total'], color='#00a8ff', linewidth=2, marker='o', markersize=4)
    ax.set_title('月度消费趋势', fontsize=16, fontweight='bold')
    ax.set_xlabel('月份')
    ax.set_ylabel('消费金额 (元)')

    step = max(1, len(df) // 12)
    ax.set_xticks(range(0, len(df), step))
    ax.set_xticklabels(df['month'].iloc[::step], rotation=45, ha='right')
    ax.grid(axis='y', alpha=0.3)

    summary = f"总记录: {len(df)} 个月 | 月均: ¥{df['total'].mean():,.0f} | 最高: ¥{df['total'].max():,.0f}"

    return fig, summary


# ========== 分析 Tab：分类统计 ==========
def analysis_category(user_id="1"):
    """消费分类饼图"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(sc.parent_category, '其他') AS category, SUM(s.amount) AS total
        FROM spending_record s
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        WHERE s.user_id = %s
        GROUP BY sc.parent_category
        ORDER BY total DESC
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None, "暂无数据"

    df = pd.DataFrame(rows)
    df['total'] = df['total'].astype(float)

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    colors = ['#00a8ff', '#4ecdc4', '#ffd93d', '#ff6b6b', '#a29bfe', '#fd79a8', '#00b894', '#e17055']
    explode = [0.02] * len(df)

    fig, ax = plt.subplots(figsize=(10, 7))
    wedges, texts, autotexts = ax.pie(
        df['total'], labels=df['category'], autopct='%1.1f%%',
        colors=colors[:len(df)], explode=explode, startangle=90,
        textprops={'fontsize': 11}
    )
    ax.set_title('消费分类分布', fontsize=16, fontweight='bold')

    summary = "消费分类占比"
    return fig, summary


# ========== 分析 Tab：商户统计 TOP10 ==========
def analysis_merchant(user_id="1"):
    """商户消费 TOP10"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(m.merchant_name, '未知商户') AS merchant, SUM(s.amount) AS total
        FROM spending_record s
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        WHERE s.user_id = %s
        GROUP BY m.merchant_name
        ORDER BY total DESC
        LIMIT 10
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None, "暂无数据"

    df = pd.DataFrame(rows)
    df['total'] = df['total'].astype(float)

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(range(len(df)), df['total'], color='#00a8ff', edgecolor='white', height=0.6)
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(df['merchant'])
    ax.set_xlabel('消费金额 (元)')
    ax.set_title('商户消费排行 TOP10', fontsize=16, fontweight='bold')
    ax.invert_yaxis()
    ax.grid(axis='x', alpha=0.3)

    for i, (bar, val) in enumerate(zip(bars, df['total'])):
        ax.text(bar.get_width() + bar.get_width() * 0.01, bar.get_y() + bar.get_height() / 2,
                f'¥{val:,.0f}', va='center', fontsize=10)

    summary = f"TOP1: {df['merchant'].iloc[0]} ¥{df['total'].iloc[0]:,.0f}"
    return fig, summary


# ========== 分析 Tab：地域统计 ==========
def analysis_region(user_id="1"):
    """地域消费统计"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT cu.city, SUM(sr.amount) AS total
        FROM spending_record sr
        LEFT JOIN consumer_unit cu ON sr.cu_id = cu.cu_id
        WHERE sr.user_id = %s
        GROUP BY cu.city
        ORDER BY total DESC
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None, "暂无数据"

    df = pd.DataFrame(rows)
    df['total'] = df['total'].astype(float)

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(range(len(df)), df['total'], color='#4ecdc4', edgecolor='white')
    ax.set_xticks(range(len(df)))
    ax.set_xticklabels(df['city'], rotation=45, ha='right')
    ax.set_ylabel('消费金额 (元)')
    ax.set_title('地域消费分布', fontsize=16, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, df['total']):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + bar.get_height() * 0.01,
                f'¥{val:,.0f}', ha='center', fontsize=9)

    summary = f"覆盖城市: {len(df)} 个"
    return fig, summary


# ========== 预测 Tab：Prophet ==========
def forecast_prophet(user_id="1", periods=120):
    """Prophet 预测"""
    try:
        from prophet import Prophet
    except ImportError:
        return None, "Prophet 未安装。\n\n请运行: pip install prophet"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT spend_date AS ds, SUM(amount) AS y
        FROM spending_record WHERE user_id = %s
        GROUP BY spend_date ORDER BY ds
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if len(rows) < 10:
        return None, f"数据不足: 仅 {len(rows)} 天"

    df = pd.DataFrame(rows)
    df['ds'] = pd.to_datetime(df['ds'])

    # Add weekly seasonality floor to avoid unrealistically low values
    min_daily = df['y'].min()

    m = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False)
    m.fit(df)
    future = m.make_future_dataframe(periods=periods)
    forecast = m.predict(future)

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(14, 6))

    ax.plot(df['ds'], df['y'], 'o', color='#00a8ff', markersize=2, alpha=0.6, label='历史数据')
    ax.plot(forecast['ds'], forecast['yhat'], color='#ff6b6b', linewidth=2, label='预测值')
    ax.fill_between(forecast['ds'], forecast['yhat_lower'].clip(lower=0),
                    forecast['yhat_upper'], color='#ff6b6b', alpha=0.15, label='置信区间')
    ax.axvline(x=df['ds'].max(), color='gray', linestyle='--', alpha=0.5, label='预测起点')
    ax.set_title('Prophet 消费趋势预测', fontsize=16, fontweight='bold')
    ax.set_xlabel('日期')
    ax.set_ylabel('消费金额 (元)')
    ax.legend(loc='upper left')
    ax.grid(alpha=0.3)

    last_hist = df['y'].mean()
    pred_avg = float(forecast['yhat'].iloc[-periods:].mean())
    trend = "上升 📈" if pred_avg > last_hist else "下降 📉"

    summary = (f"历史天数: {len(df)} | 预测天数: {periods}\n"
               f"历史日均: ¥{last_hist:,.0f} | 预测日均: ¥{pred_avg:,.0f} | 趋势: {trend}")

    return fig, summary


# ========== 预测 Tab：LSTM ==========
def forecast_lstm(user_id="1", periods=60):
    """LSTM 预测"""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return None, "PyTorch 未安装。\n\n请运行: pip install torch"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT spend_date, SUM(amount) AS y
        FROM spending_record WHERE user_id = %s
        GROUP BY spend_date ORDER BY spend_date
    """, (int(user_id),))
    rows = cur.fetchall()
    conn.close()

    if len(rows) < 15:
        return None, f"LSTM 至少需要 15 天数据，当前: {len(rows)} 天"

    df = pd.DataFrame(rows)
    data_vals = df['y'].values.astype(np.float64)
    data_min, data_max = float(data_vals.min()), float(data_vals.max())

    if data_max - data_min < 0.01:
        return None, "数据变化太小，无法预测"

    data_norm = (data_vals - data_min) / (data_max - data_min)
    seq_len = min(10, max(3, len(data_norm) // 3))

    X, Y = [], []
    for i in range(len(data_norm) - seq_len):
        X.append(data_norm[i:i + seq_len])
        Y.append(data_norm[i + seq_len])

    if len(X) < 5:
        return None, "训练样本不足"

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    class LSTMModel(nn.Module):
        def __init__(self, input_size=1, hidden_size=32, num_layers=1, output_size=1):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.1)
            self.fc = nn.Linear(hidden_size, output_size)

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])

    hidden_size = min(32, max(8, len(data_norm) // 4))
    X_tensor = torch.tensor(np.array(X)).unsqueeze(-1).float().to(device)
    Y_tensor = torch.tensor(np.array(Y)).float().to(device)

    model = LSTMModel(input_size=1, hidden_size=hidden_size, output_size=1).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005)
    criterion = nn.MSELoss()

    model.train()
    epochs = min(60, max(20, len(X) * 2))
    for _ in range(epochs):
        outputs = model(X_tensor)
        loss = criterion(outputs.squeeze(), Y_tensor)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    model.eval()
    last_seq = data_norm[-seq_len:].copy()
    future_preds = []
    pred_count = min(periods, 60)
    for _ in range(pred_count):
        with torch.no_grad():
            inp = torch.tensor(last_seq).unsqueeze(0).unsqueeze(-1).float().to(device)
            pred = model(inp).item()
            future_preds.append(float(max(0, pred * (data_max - data_min) + data_min)))
            last_seq = np.roll(last_seq, -1)
            last_seq[-1] = pred

    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    last_date = df['spend_date'].max()
    if hasattr(last_date, 'to_pydatetime'):
        last_date = last_date.to_pydatetime()
    elif isinstance(last_date, datetime):
        pass
    else:
        last_date = datetime.strptime(str(last_date), '%Y-%m-%d') if isinstance(last_date, str) else datetime.now()

    future_dates = [last_date + timedelta(days=i + 1) for i in range(len(future_preds))]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(df['spend_date'], data_vals, color='#00a8ff', linewidth=1.5, alpha=0.8, label='历史数据')
    ax.plot(future_dates, future_preds, color='#ff6b6b', linewidth=2, linestyle='--', label='LSTM 预测')
    ax.axvline(x=last_date, color='gray', linestyle='--', alpha=0.5)
    ax.fill_between(future_dates, 0, future_preds, color='#ff6b6b', alpha=0.1)
    ax.set_title('LSTM 消费趋势预测', fontsize=16, fontweight='bold')
    ax.set_xlabel('日期')
    ax.set_ylabel('消费金额 (元)')
    ax.legend(loc='upper left')
    ax.grid(alpha=0.3)

    hist_avg = float(data_vals.mean())
    pred_avg = float(np.mean(future_preds))
    trend = "上升 📈" if pred_avg > hist_avg else "下降 📉"

    summary = (f"历史天数: {len(data_vals)} | 预测天数: {len(future_preds)}\n"
               f"历史日均: ¥{hist_avg:,.0f} | 预测日均: ¥{pred_avg:,.0f} | 趋势: {trend}")

    return fig, summary


# ========== 数据查询 Tab ==========
def query_data(user_id="1", start_date="2024-07-01", end_date="2026-07-08",
               category_filter="全部", merchant_filter="全部", min_amount=0, max_amount=99999, limit=500):
    """查询消费记录"""
    conn = get_conn()
    cur = conn.cursor()

    conditions = ["s.user_id = %s"]
    params = [int(user_id)]

    if start_date and start_date.strip():
        conditions.append("s.spend_date >= %s")
        params.append(start_date)
    if end_date and end_date.strip():
        conditions.append("s.spend_date <= %s")
        params.append(end_date)
    if category_filter and category_filter != "全部":
        conditions.append("sc.parent_category = %s")
        params.append(category_filter)
    if merchant_filter and merchant_filter != "全部":
        conditions.append("m.merchant_name = %s")
        params.append(merchant_filter)
    if min_amount > 0:
        conditions.append("s.amount >= %s")
        params.append(min_amount)
    if max_amount < 99999:
        conditions.append("s.amount <= %s")
        params.append(max_amount)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT s.spend_date, s.amount, COALESCE(sc.category_name, '') AS category,
               COALESCE(m.merchant_name, '') AS merchant, COALESCE(cu.city, '') AS city,
               s.payment_method, s.remarks
        FROM spending_record s
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        LEFT JOIN consumer_unit cu ON s.cu_id = cu.cu_id
        WHERE {where}
        ORDER BY s.spend_date DESC
        LIMIT %s
    """
    params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame(), "无匹配记录"

    df = pd.DataFrame(rows)
    return df, f"查询结果: {len(df)} 条 (限制 {limit})"


def get_query_filters(user_id="1"):
    """获取筛选下拉选项"""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT sc.parent_category
        FROM spending_record s
        JOIN spending_category sc ON s.category_id = sc.category_id
        WHERE s.user_id = %s AND sc.parent_category IS NOT NULL
        ORDER BY sc.parent_category
    """, (int(user_id),))
    cats = ["全部"] + [r['parent_category'] for r in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT m.merchant_name
        FROM spending_record s
        JOIN merchant m ON s.merchant_id = m.merchant_id
        WHERE s.user_id = %s
        ORDER BY m.merchant_name
    """, (int(user_id),))
    merchants = ["全部"] + [r['merchant_name'] for r in cur.fetchall()]

    conn.close()
    return cats, merchants


# ========== 用户选择器 ==========
def get_user_list():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, full_name, username FROM users WHERE status = 1 ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [(f"{r['full_name']} ({r['username']})", str(r['id'])) for r in rows]


# ========== 构建 Gradio 界面 ==========
def build_ui():
    users = get_user_list()
    default_user = users[0][1] if users else "1"

    cats, merchants = get_query_filters(default_user)

    with gr.Blocks(title="消费数据分析预测工作台", theme=gr.themes.Soft()) as demo:
        gr.Markdown("""
        # 📊 消费数据分析预测工作台
        ### 基于 PostgreSQL + Prophet + LSTM | 作者：张跃星
        """)

        # ---- 用户选择器 ----
        with gr.Row():
            user_dropdown = gr.Dropdown(choices=users, value=default_user, label="选择用户", scale=1)

        # ---- Tab 1: 消费分析 ----
        with gr.Tab("📈 消费分析"):
            gr.Markdown("### 多维度消费数据分析")
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("**📊 月度消费趋势**")
                    plot_trend = gr.Plot(label="")
                    text_trend = gr.Textbox(label="汇总", lines=2, interactive=False)

                with gr.Column(scale=1):
                    gr.Markdown("**🥧 消费分类分布**")
                    plot_category = gr.Plot(label="")
                    text_category = gr.Textbox(label="汇总", lines=1, interactive=False)

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("**🏪 商户消费排行 TOP10**")
                    plot_merchant = gr.Plot(label="")
                    text_merchant = gr.Textbox(label="汇总", lines=1, interactive=False)

                with gr.Column(scale=1):
                    gr.Markdown("**📍 地域消费分布**")
                    plot_region = gr.Plot(label="")
                    text_region = gr.Textbox(label="汇总", lines=1, interactive=False)

            btn_analysis = gr.Button("🔄 刷新分析", variant="primary")
            btn_analysis.click(
                fn=analysis_trend, inputs=[user_dropdown], outputs=[plot_trend, text_trend]
            ).then(
                fn=analysis_category, inputs=[user_dropdown], outputs=[plot_category, text_category]
            ).then(
                fn=analysis_merchant, inputs=[user_dropdown], outputs=[plot_merchant, text_merchant]
            ).then(
                fn=analysis_region, inputs=[user_dropdown], outputs=[plot_region, text_region]
            )

        # ---- Tab 2: 趋势预测 ----
        with gr.Tab("🔮 趋势预测"):
            gr.Markdown("### 机器学习消费趋势预测")

            with gr.Row():
                periods_slider = gr.Slider(minimum=7, maximum=365, value=120, step=7, label="预测天数")

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### Prophet 预测")
                    plot_prophet = gr.Plot(label="")
                    text_prophet = gr.Textbox(label="Prophet 汇总", lines=3, interactive=False)
                    btn_prophet = gr.Button("🚀 运行 Prophet", variant="primary")

                with gr.Column(scale=1):
                    gr.Markdown("#### LSTM 预测")
                    plot_lstm = gr.Plot(label="")
                    text_lstm = gr.Textbox(label="LSTM 汇总", lines=3, interactive=False)
                    btn_lstm = gr.Button("🚀 运行 LSTM", variant="primary")

            btn_prophet.click(
                fn=forecast_prophet, inputs=[user_dropdown, periods_slider], outputs=[plot_prophet, text_prophet]
            )
            btn_lstm.click(
                fn=forecast_lstm, inputs=[user_dropdown, periods_slider], outputs=[plot_lstm, text_lstm]
            )

        # ---- Tab 3: 数据查询 ----
        with gr.Tab("🔍 数据查询"):
            gr.Markdown("### 消费记录查询与导出")

            with gr.Row():
                start_input = gr.Textbox(label="开始日期", value="2024-07-01", placeholder="YYYY-MM-DD")
                end_input = gr.Textbox(label="结束日期", value="2026-07-08", placeholder="YYYY-MM-DD")
                cat_dropdown = gr.Dropdown(choices=cats, value="全部", label="消费分类")
                merchant_dropdown = gr.Dropdown(choices=merchants, value="全部", label="商户")

            with gr.Row():
                min_amt = gr.Number(label="最低金额", value=0, precision=0)
                max_amt = gr.Number(label="最高金额", value=99999, precision=0)
                limit_slider = gr.Slider(minimum=10, maximum=2000, value=500, step=10, label="返回条数")

            btn_query = gr.Button("🔍 执行查询", variant="primary")
            query_table = gr.Dataframe(label="查询结果", interactive=False)
            query_info = gr.Textbox(label="查询信息", lines=1, interactive=False)

            btn_query.click(
                fn=query_data,
                inputs=[user_dropdown, start_input, end_input, cat_dropdown, merchant_dropdown, min_amt, max_amt, limit_slider],
                outputs=[query_table, query_info]
            )

            # Refresh filters when user changes
            user_dropdown.change(
                fn=lambda uid: get_query_filters(uid),
                inputs=[user_dropdown],
                outputs=[cat_dropdown, merchant_dropdown]
            )

        # ---- 自动加载初始数据 ----
        demo.load(
            fn=analysis_trend, inputs=[user_dropdown], outputs=[plot_trend, text_trend]
        ).then(
            fn=analysis_category, inputs=[user_dropdown], outputs=[plot_category, text_category]
        ).then(
            fn=analysis_merchant, inputs=[user_dropdown], outputs=[plot_merchant, text_merchant]
        ).then(
            fn=analysis_region, inputs=[user_dropdown], outputs=[plot_region, text_region]
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="0.0.0.0", server_port=7860, share=False)
