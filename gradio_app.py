# -*- coding: utf-8 -*-
"""
消费分析预测工作台 - Gradio 版本
作者：张跃星
功能：消费数据分析（趋势/分类/商户/地域）+ 趋势预测 + 6大算法模块
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_runtime_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')
os.environ.setdefault('MPLCONFIGDIR', os.path.join(_runtime_cache, 'matplotlib'))
os.environ.setdefault('GRADIO_TEMP_DIR', os.path.join(_runtime_cache, 'gradio'))
os.environ.setdefault('LOKY_MAX_CPU_COUNT', str(min(os.cpu_count() or 1, 4)))
os.makedirs(os.environ['MPLCONFIGDIR'], exist_ok=True)
os.makedirs(os.environ['GRADIO_TEMP_DIR'], exist_ok=True)

import gradio as gr
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import warnings
import logging
import time
import json
from datetime import datetime
warnings.filterwarnings('ignore')

# ==================== 日志系统 ====================
log = logging.getLogger('lstm_predictor')
log.setLevel(logging.DEBUG)
log.propagate = False  # 不传播到 root logger，避免捕获 Gradio 内部日志

import io
_log_buffer = io.StringIO()
_log_handler = logging.StreamHandler(_log_buffer)
_log_handler._is_lstm_buffer = True
_log_handler.setLevel(logging.DEBUG)
_log_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)-7s] %(message)s', datefmt='%H:%M:%S'))

# 同时添加一个控制台 handler 用于终端实时查看
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)-7s] %(message)s', datefmt='%H:%M:%S'))

log.addHandler(_log_handler)
log.addHandler(_console_handler)


def _capture_logs():
    """清空缓冲区，准备捕获新日志"""
    global _log_buffer
    _log_buffer = io.StringIO()
    # 移除指向旧缓冲区的 handler
    for h in log.handlers[:]:
        if isinstance(h, logging.StreamHandler) and getattr(h, '_is_lstm_buffer', False):
            log.removeHandler(h)
    # 添加指向新缓冲区的 handler
    _buf_handler = logging.StreamHandler(_log_buffer)
    _buf_handler._is_lstm_buffer = True
    _buf_handler.setLevel(logging.DEBUG)
    _buf_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)-7s] %(message)s', datefmt='%H:%M:%S'))
    log.addHandler(_buf_handler)
    return ''


def _get_logs():
    """获取捕获的日志"""
    buf = _log_buffer
    if hasattr(buf, 'getvalue'):
        return buf.getvalue()
    return ''

# ---- Chinese font setup ----
_zh_fonts = [f for f in font_manager.findSystemFonts() if 'msyh' in f.lower() or 'simhei' in f.lower() or 'simsun' in f.lower() or 'noto sans cjk' in f.lower() or 'wqy' in f.lower()]
if not _zh_fonts:
    try:
        _zh_fonts = [font_manager.findfont('Microsoft YaHei', fallback_to_default=False)]
    except Exception:
        _zh_fonts = []
if _zh_fonts:
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans']
else:
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ---- DB ----
import psycopg2
from psycopg2.extras import RealDictCursor
from sqlalchemy.engine import make_url
from app.config import Config


def _db_connection():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    return psycopg2.connect(
        host=url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
        cursor_factory=RealDictCursor,
    )


def query(sql, params=None):
    connection = _db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        connection.close()


def execute(sql, params=None):
    connection = _db_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params or ())
                return cursor.rowcount
    finally:
        connection.close()

# ---- Color palette ----
C_BLUE = '#00a8ff'
C_GREEN = '#4ecdc4'
C_ORANGE = '#ff9f43'
C_RED = '#ff6b6b'
C_PURPLE = '#a29bfe'
C_PINK = '#fd79a8'
C_YELLOW = '#ffd93d'
COLORS = [C_BLUE, C_GREEN, C_ORANGE, C_RED, C_PURPLE, C_PINK, C_YELLOW, '#00d2d3', '#54a0ff', '#5f27cd']
BG = '#1a1a1a'
FG = '#ffffff'


def dark_style(fig, ax):
    fig.patch.set_facecolor(BG)
    ax.set_facecolor('#252525')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_color('#444')
    ax.spines['left'].set_color('#444')
    ax.tick_params(colors='#aaa', labelsize=9)
    ax.xaxis.label.set_color('#aaa')
    ax.yaxis.label.set_color('#aaa')
    ax.title.set_color(FG)
    ax.grid(True, color='#333', linewidth=0.5, alpha=0.5)


# ==================== 消费分析 ====================

def analysis_plot(view_type, user_name):
    if not user_name or user_name == '全部用户':
        user_filter = ''
        params = ()
    else:
        user_filter = 'AND u.username = %s'
        params = (user_name,)

    fig, ax = plt.subplots(figsize=(12, 6))

    if view_type == '消费趋势（按月）':
        sql = f"""
            SELECT TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE,'YYYY-MM') AS mth,
                   SUM(s.amount) AS total
            FROM spending_record s JOIN users u ON s.user_id=u.id
            WHERE 1=1 {user_filter}
            GROUP BY DATE_TRUNC('month',s.spend_date) ORDER BY mth
        """
        rows = query(sql, params)
        if not rows:
            return fig, "暂无数据"
        months = [r['mth'] for r in rows]
        totals = [float(r['total']) for r in rows]
        ax.plot(range(len(months)), totals, color=C_BLUE, linewidth=2, marker='o', markersize=4)
        ax.fill_between(range(len(months)), 0, totals, alpha=0.15, color=C_BLUE)
        ax.set_xticks(range(0, len(months), max(1, len(months)//12)))
        ax.set_xticklabels([months[i] for i in range(0, len(months), max(1, len(months)//12))], rotation=45, fontsize=8)
        ax.set_ylabel('消费金额 (元)')
        ax.set_title(f'消费趋势 - {user_name}')

    elif view_type == '消费分类统计':
        sql = f"""
            SELECT COALESCE(sc.parent_category, '其他') AS cat, SUM(s.amount) AS total
            FROM spending_record s LEFT JOIN spending_category sc ON s.category_id=sc.category_id
            JOIN users u ON s.user_id=u.id
            WHERE 1=1 {user_filter}
            GROUP BY sc.parent_category ORDER BY total DESC
        """
        rows = query(sql, params)
        if not rows:
            return fig, "暂无数据"
        cats = [r['cat'] for r in rows]
        totals = [float(r['total']) for r in rows]
        bars = ax.bar(range(len(cats)), totals, color=COLORS[:len(cats)])
        for bar, val in zip(bars, totals):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+max(totals)*0.01,
                    f'¥{val:,.0f}', ha='center', fontsize=8, color='#aaa')
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels(cats, rotation=30, ha='right', fontsize=9)
        ax.set_ylabel('消费金额 (元)')
        ax.set_title(f'消费分类统计 - {user_name}')

    elif view_type == '商户消费排行':
        sql = f"""
            SELECT m.merchant_name, SUM(s.amount) AS total
            FROM spending_record s JOIN merchant m ON s.merchant_id=m.merchant_id
            JOIN users u ON s.user_id=u.id
            WHERE 1=1 {user_filter}
            GROUP BY m.merchant_name ORDER BY total DESC LIMIT 15
        """
        rows = query(sql, params)
        if not rows:
            return fig, "暂无数据"
        names = [r['merchant_name'] for r in rows]
        totals = [float(r['total']) for r in rows]
        ax.barh(range(len(names)), totals[::-1], color=[C_BLUE]*len(names))
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names[::-1], fontsize=9)
        ax.set_xlabel('消费金额 (元)')
        ax.set_title(f'商户消费 Top15 - {user_name}')

    elif view_type == '地域消费分布':
        sql = f"""
            SELECT cu.city AS city, SUM(s.amount) AS total
            FROM spending_record s JOIN consumer_unit cu ON s.cu_id=cu.cu_id
            JOIN users u ON s.user_id=u.id
            WHERE 1=1 {user_filter}
            GROUP BY cu.city ORDER BY total DESC
        """
        rows = query(sql, params)
        if not rows:
            return fig, "暂无数据"
        cities = [r['city'] for r in rows]
        totals = [float(r['total']) for r in rows]
        explode = [0.03]*len(cities)
        if totals:
            explode[totals.index(max(totals))] = 0.1
        wedges, texts, autotexts = ax.pie(totals, labels=cities, autopct='%1.1f%%',
            colors=COLORS[:len(cities)], explode=explode, textprops={'color': FG, 'fontsize': 9})
        for at in autotexts:
            at.set_fontsize(8)
        ax.set_title(f'地域消费分布 - {user_name}')

    dark_style(fig, ax)
    plt.tight_layout()
    return fig, f"共 {len(rows)} 条记录" if rows else "暂无数据"


# ==================== 趋势预测 ====================

def forecast_run(user_name, model_type, periods):
    _capture_logs()  # 每次预测前清空日志缓冲区
    if not user_name or user_name == '全部用户':
        user_filter = ''
        params = ()
    else:
        user_filter = 'AND u.username = %s'
        params = (user_name,)

    sql = f"""
        SELECT s.spend_date, SUM(s.amount) AS total
        FROM spending_record s JOIN users u ON s.user_id=u.id
        WHERE 1=1 {user_filter}
        GROUP BY s.spend_date ORDER BY s.spend_date
    """
    rows = query(sql, params)
    if not rows or len(rows) < 10:
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.text(0.5, 0.5, '历史数据不足（至少需要10天记录）', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        return fig, "数据不足", _get_logs()
    df = pd.DataFrame([(r['spend_date'], float(r['total'])) for r in rows], columns=['ds', 'y'])
    df['ds'] = pd.to_datetime(df['ds'])
    periods = min(int(periods), 365)

    fig, ax = plt.subplots(figsize=(13, 6.5))

    if model_type == 'Prophet':
        try:
            from prophet import Prophet
        except ImportError:
            ax.text(0.5, 0.5, 'Prophet 未安装\n请运行: pip install prophet', ha='center', va='center', fontsize=14, color=C_RED, transform=ax.transAxes)
            dark_style(fig, ax)
            return fig, "Prophet 未安装", _get_logs()

        m = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False)
        m.fit(df)
        future = m.make_future_dataframe(periods=periods)
        forecast = m.predict(future)

        ax.plot(df['ds'], df['y'], color=C_BLUE, linewidth=2, label='历史数据')
        ax.plot(forecast['ds'], forecast['yhat'], color=C_RED, linewidth=2, linestyle='--', label='预测值')
        ax.fill_between(forecast['ds'], forecast['yhat_lower'], forecast['yhat_upper'],
                        color=C_RED, alpha=0.12, label='置信区间')
        ax.legend(loc='upper left', framealpha=0.8, facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG)
        msg = f"Prophet | 历史{len(df)}天 | 预测{periods}天 | 日均 ¥{df['y'].mean():.0f}"

    else:  # LSTM
        t_start = time.time()
        log.info("=" * 70)
        log.info("LSTM 趋势预测启动 | 用户: %s | 请求预测天数: %d", user_name, periods)
        log.info("=" * 70)

        # ---- 阶段1: 检查依赖 ----
        log.info("[阶段1/8] 检查 PyTorch 依赖...")
        try:
            import torch
            import torch.nn as nn
            log.info("  PyTorch 版本: %s", torch.__version__)
            log.info("  CUDA 可用: %s | 设备数: %d", torch.cuda.is_available(), torch.cuda.device_count() if torch.cuda.is_available() else 0)
        except ImportError as e:
            log.error("[阶段1/8] PyTorch 未安装: %s", e)
            ax.text(0.5, 0.5, 'PyTorch 未安装\n请运行: pip install torch', ha='center', va='center', fontsize=14, color=C_RED, transform=ax.transAxes)
            dark_style(fig, ax)
            return fig, "PyTorch 未安装", _get_logs()

        class LSTMModel(nn.Module):
            def __init__(self, input_size=1, hidden_size=32, output_size=1):
                super().__init__()
                self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True, dropout=0.1)
                self.fc = nn.Linear(hidden_size, output_size)
            def forward(self, x):
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])

        # ---- 阶段2: 数据检查 ----
        log.info("[阶段2/8] 数据检查...")
        data_vals = df['y'].values.astype(np.float64)
        d_min, d_max = float(data_vals.min()), float(data_vals.max())
        d_mean, d_std = float(data_vals.mean()), float(data_vals.std())
        log.info("  数据点数: %d", len(data_vals))
        log.info("  日期范围: %s ~ %s", str(df['ds'].min())[:10], str(df['ds'].max())[:10])
        log.info("  数值范围: min=%.2f  max=%.2f  mean=%.2f  std=%.2f", d_min, d_max, d_mean, d_std)
        log.info("  数据跨度 (max-min): %.4f", d_max - d_min)

        # 输出前5和后5个值
        log.info("  前5个值: %s", [round(v, 2) for v in data_vals[:5].tolist()])
        log.info("  后5个值: %s", [round(v, 2) for v in data_vals[-5:].tolist()])

        if d_max - d_min < 0.01:
            log.error("[阶段2/8] 数据变化太小 (%.4f < 0.01)，无法训练", d_max - d_min)
            ax.text(0.5, 0.5, '数据变化太小，无法预测', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
            dark_style(fig, ax)
            return fig, "数据变化太小", _get_logs()

        # ---- 阶段3: 归一化 ----
        log.info("[阶段3/8] 数据归一化...")
        data_norm = (data_vals - d_min) / (d_max - d_min)
        log.info("  归一化后范围: min=%.6f  max=%.6f", float(data_norm.min()), float(data_norm.max()))
        log.info("  归一化后均值: %.6f  标准差: %.6f", float(data_norm.mean()), float(data_norm.std()))

        # ---- 阶段4: 构建序列 ----
        log.info("[阶段4/8] 构建训练序列...")
        seq_len = min(10, max(3, len(data_norm) // 3))
        log.info("  序列窗口长度 (seq_len): %d (计算: min(10, max(3, %d//3)))", seq_len, len(data_norm))

        X, Y = [], []
        for i in range(len(data_norm) - seq_len):
            X.append(data_norm[i:i + seq_len])
            Y.append(data_norm[i + seq_len])
        log.info("  训练样本数: X=%d  Y=%d", len(X), len(Y))

        if len(X) < 5:
            log.error("[阶段4/8] 训练样本不足 (X=%d < 5)", len(X))
            ax.text(0.5, 0.5, '数据量不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
            dark_style(fig, ax)
            return fig, "数据不足", _get_logs()

        log.info("  X 形状: (%d, %d) | Y 形状: (%d,)", len(X), seq_len, len(Y))
        log.info("  X[0] 示例: %s", [round(v, 4) for v in X[0]])
        log.info("  Y[0] 示例: %.4f", Y[0])

        # ---- 阶段5: 构建模型 ----
        log.info("[阶段5/8] 构建 Tensor 和 LSTM 模型...")
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        log.info("  计算设备: %s", str(device))

        X_t = torch.tensor(np.array(X)).unsqueeze(-1).float().to(device)
        Y_t = torch.tensor(np.array(Y)).float().to(device)
        log.info("  Tensor 形状 → X_t: %s  Y_t: %s", tuple(X_t.shape), tuple(Y_t.shape))
        log.info("  X_t 数据类型: %s | Y_t 数据类型: %s", X_t.dtype, Y_t.dtype)
        log.info("  X_t 设备: %s | Y_t 设备: %s", X_t.device, Y_t.device)

        hidden_size = min(32, max(8, len(X) // 2))
        model = LSTMModel(input_size=1, hidden_size=hidden_size, output_size=1).to(device)
        log.info("  模型结构: LSTM(in=1, hidden=%d) → Linear(%d→1)", hidden_size, hidden_size)
        log.info("  模型参数量: %d", sum(p.numel() for p in model.parameters()))

        opt = torch.optim.Adam(model.parameters(), lr=0.005)
        criterion = nn.MSELoss()
        log.info("  优化器: Adam(lr=0.005) | 损失函数: MSELoss")

        # ---- 阶段6: 训练 ----
        epochs = min(80, max(30, len(X) * 2))
        log.info("[阶段6/8] 开始训练 (epochs=%d)...", epochs)
        model.train()

        class LossTracker:
            def __init__(self):
                self.losses = []
            def __call__(self, epoch, loss_val):
                self.losses.append(loss_val)
                if epoch < 3 or epoch >= epochs - 2 or epoch % max(1, epochs // 8) == 0:
                    log.info("  Epoch %3d/%d | Loss: %.8f", epoch + 1, epochs, loss_val)

        tracker = LossTracker()
        for epoch in range(epochs):
            out = model(X_t)
            loss = criterion(out.squeeze(), Y_t)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tracker(epoch, loss.item())

        final_loss = loss.item()
        log.info("  最终 Loss: %.8f | 首 Loss: %.8f | 改善: %.2f%%",
                 final_loss, tracker.losses[0] if tracker.losses else -1,
                 100 * (1 - final_loss / tracker.losses[0]) if tracker.losses and tracker.losses[0] else 0)
        log.info("  Loss 趋势: 前3项=%s  后3项=%s",
                 [round(l, 6) for l in tracker.losses[:3]],
                 [round(l, 6) for l in tracker.losses[-3:]])

        # ---- 阶段7: 预测 ----
        log.info("[阶段7/8] 执行预测 (periods=%d)...", periods)
        model.eval()
        last_seq = data_norm[-seq_len:].copy()
        log.info("  初始输入序列 (归一化): %s", [round(v, 4) for v in last_seq])

        future_preds = []
        pred_n = min(int(periods), 60)
        log.info("  实际预测步数: %d", pred_n)

        for step in range(pred_n):
            with torch.no_grad():
                inp = torch.tensor(last_seq).unsqueeze(0).unsqueeze(-1).float().to(device)
                p = model(inp).item()
                raw_val = p * (d_max - d_min) + d_min
                clipped_val = float(max(0, raw_val))
                future_preds.append(clipped_val)

                if step < 3 or step >= pred_n - 2:
                    log.info("  预测步骤 %2d: 归一化值=%.6f  反归一化=%.2f  最终值=%.2f",
                             step + 1, p, raw_val, clipped_val)

                last_seq = np.roll(last_seq, -1)
                last_seq[-1] = p

        log.info("  预测值列表 (前5): %s", [round(v, 2) for v in future_preds[:5]])
        log.info("  预测值列表 (后5): %s", [round(v, 2) for v in future_preds[-5:]])
        log.info("  预测值范围: min=%.2f  max=%.2f  mean=%.2f", min(future_preds), max(future_preds), np.mean(future_preds))

        # ---- 阶段8: 绘制图表 ----
        log.info("[阶段8/8] 绘制结果图表...")
        last_date = df['ds'].max().to_pydatetime() if hasattr(df['ds'].max(), 'to_pydatetime') else df['ds'].max()
        future_dates = pd.date_range(start=last_date + pd.Timedelta(days=1), periods=len(future_preds))
        log.info("  最后历史日期: %s  预测起始: %s  预测结束: %s",
                 str(last_date)[:10], str(future_dates[0])[:10], str(future_dates[-1])[:10])

        ax.plot(df['ds'], df['y'], color=C_BLUE, linewidth=2, label='历史数据')
        ax.plot(future_dates, future_preds, color=C_RED, linewidth=2, linestyle='--', marker='s', markersize=3, label='LSTM 预测')
        ax.axvline(x=df['ds'].max(), color='#666', linewidth=0.8, linestyle=':')
        ax.legend(loc='upper left', framealpha=0.8, facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG)

        elapsed = time.time() - t_start
        log.info("=" * 70)
        log.info("LSTM 预测完成! 总耗时: %.2f秒", elapsed)
        log.info("=" * 70)

        msg = (f"LSTM 预测完成 | 历史{len(df)}天 → 预测{pred_n}天 | "
               f"训练 {epochs} epochs | Loss: {final_loss:.6f} | 耗时 {elapsed:.1f}秒")

    dark_style(fig, ax)
    ax.set_ylabel('消费金额 (元)')
    ax.set_title(f'趋势预测 - {user_name} ({model_type})')
    ax.set_xlabel('日期')
    plt.tight_layout()
    return fig, msg, _get_logs()


# ==================== 数据概览 ====================

def data_summary(user_name):
    if not user_name or user_name == '全部用户':
        rows = query("""
            SELECT COUNT(*) as cnt, SUM(amount) as total,
                   ROUND(AVG(amount),2) as avg_amt,
                   MIN(spend_date) as first, MAX(spend_date) as last
            FROM spending_record
        """)
        top_cats = query("""
            SELECT COALESCE(sc.parent_category,'其他') as cat, SUM(s.amount) as total
            FROM spending_record s LEFT JOIN spending_category sc ON s.category_id=sc.category_id
            GROUP BY sc.parent_category ORDER BY total DESC LIMIT 5
        """)
    else:
        rows = query("""
            SELECT COUNT(*) as cnt, SUM(amount) as total,
                   ROUND(AVG(amount),2) as avg_amt,
                   MIN(spend_date) as first, MAX(spend_date) as last
            FROM spending_record WHERE user_id = (SELECT id FROM users WHERE username=%s)
        """, (user_name,))
        top_cats = query("""
            SELECT COALESCE(sc.parent_category,'其他') as cat, SUM(s.amount) as total
            FROM spending_record s LEFT JOIN spending_category sc ON s.category_id=sc.category_id
            WHERE s.user_id = (SELECT id FROM users WHERE username=%s)
            GROUP BY sc.parent_category ORDER BY total DESC LIMIT 5
        """, (user_name,))

    r = rows[0] if rows else {}
    md = f"""
## 📊 数据概览 - {user_name}

| 指标 | 数值 |
|------|------|
| **总消费笔数** | {r.get('cnt', 0):,} 笔 |
| **累计消费金额** | ¥{float(r.get('total', 0)):,.2f} |
| **笔均消费** | ¥{float(r.get('avg_amt', 0)):,.2f} |
| **数据范围** | {str(r.get('first', '-'))} ~ {str(r.get('last', '-'))} |
"""
    if top_cats:
        md += "\n### Top 5 消费分类\n"
        for i, tc in enumerate(top_cats):
            md += f"{i+1}. **{tc['cat']}**: ¥{float(tc['total']):,.0f}\n"
    return md


# ==================== RFM 分析 ====================

def rfm_analysis():
    rows = query("""
        SELECT s.user_id, u.username,
               CURRENT_DATE - MAX(s.spend_date)::DATE AS r_days,
               COUNT(*) AS f_count,
               SUM(s.amount) AS m_amount
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY s.user_id, u.username
    """)

    if not rows or len(rows) < 5:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, '用户数据不足（至少5个用户）', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        fig_bar, ax_bar = plt.subplots(figsize=(10, 5))
        ax_bar.text(0.5, 0.5, '用户数据不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax_bar.transAxes)
        dark_style(fig_bar, ax_bar)
        plt.tight_layout()
        return fig, fig_bar, pd.DataFrame(columns=['用户', 'R(天)', 'F(次)', 'M(元)', 'R分', 'F分', 'M分', 'RFM总分', '客户分类']), "用户数据不足（至少需要5个用户）"

    df = pd.DataFrame([(r['user_id'], r['username'], int(r['r_days']), int(r['f_count']),
                         float(r['m_amount'])) for r in rows],
                       columns=['user_id', 'username', 'r_days', 'f_count', 'm_amount'])

    # R值反向打分（天数越近越好，越小分越高）
    try:
        df['r_score'] = pd.qcut(df['r_days'], q=5, labels=[5, 4, 3, 2, 1]).astype(int)
    except Exception:
        df['r_score'] = pd.cut(df['r_days'], bins=5, labels=[5, 4, 3, 2, 1]).astype(int)

    try:
        df['f_score'] = pd.qcut(df['f_count'], q=5, labels=[1, 2, 3, 4, 5]).astype(int)
    except Exception:
        df['f_score'] = pd.cut(df['f_count'], bins=5, labels=[1, 2, 3, 4, 5]).astype(int)

    try:
        df['m_score'] = pd.qcut(df['m_amount'], q=5, labels=[1, 2, 3, 4, 5]).astype(int)
    except Exception:
        df['m_score'] = pd.cut(df['m_amount'], bins=5, labels=[1, 2, 3, 4, 5]).astype(int)

    df['rfm_score'] = df['r_score'] + df['f_score'] + df['m_score']

    def classify_rfm(s):
        if s >= 12:
            return '高价值客户'
        elif s >= 8:
            return '中价值客户'
        elif s >= 4:
            return '低价值客户'
        else:
            return '流失客户'

    df['segment'] = df['rfm_score'].apply(classify_rfm)

    seg_colors = {'高价值客户': C_GREEN, '中价值客户': C_BLUE, '低价值客户': C_ORANGE, '流失客户': C_RED}

    # Scatter plot: R vs F
    fig, ax = plt.subplots(figsize=(10, 6))
    for seg in ['高价值客户', '中价值客户', '低价值客户', '流失客户']:
        subset = df[df['segment'] == seg]
        if len(subset) > 0:
            ax.scatter(subset['r_days'], subset['f_count'], c=seg_colors[seg], label=seg,
                       s=80, alpha=0.8, edgecolors='white', linewidth=0.5)
    ax.set_xlabel('最近消费距今 (天)')
    ax.set_ylabel('消费频次 (笔)')
    ax.set_title('RFM 分析 - 用户散点图')
    ax.legend(loc='upper right', facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG)
    dark_style(fig, ax)
    plt.tight_layout()

    # Bar chart: segment counts
    fig_bar, ax_bar = plt.subplots(figsize=(10, 5))
    seg_counts = df['segment'].value_counts()
    seg_order = ['高价值客户', '中价值客户', '低价值客户', '流失客户']
    counts = [seg_counts.get(s, 0) for s in seg_order]
    bars = ax_bar.bar(seg_order, counts, color=[seg_colors[s] for s in seg_order])
    for bar, val in zip(bars, counts):
        ax_bar.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                    str(val), ha='center', fontsize=11, color=FG)
    ax_bar.set_ylabel('用户数量')
    ax_bar.set_title('RFM 客户分类分布')
    dark_style(fig_bar, ax_bar)
    plt.tight_layout()

    # Table data
    table_df = df[['username', 'r_days', 'f_count', 'm_amount', 'r_score', 'f_score', 'm_score', 'rfm_score', 'segment']].copy()
    table_df.columns = ['用户', 'R(天)', 'F(次)', 'M(元)', 'R分', 'F分', 'M分', 'RFM总分', '客户分类']
    table_df['M(元)'] = table_df['M(元)'].round(2)

    msg = f"RFM分析完成 | 总用户: {len(df)} | 高价值: {counts[0]} | 中价值: {counts[1]} | 低价值: {counts[2]} | 流失: {counts[3]}"
    return fig, fig_bar, table_df, msg


def rfm_save():
    try:
        rows = query("""
            SELECT s.user_id, u.username,
                   CURRENT_DATE - MAX(s.spend_date)::DATE AS r_days,
                   COUNT(*) AS f_count,
                   SUM(s.amount) AS m_amount
            FROM spending_record s
            JOIN users u ON s.user_id = u.id
            GROUP BY s.user_id, u.username
        """)
        if not rows:
            return "没有数据可保存"

        df = pd.DataFrame([(r['user_id'], r['username'], int(r['r_days']), int(r['f_count']),
                             float(r['m_amount'])) for r in rows],
                           columns=['user_id', 'username', 'r_days', 'f_count', 'm_amount'])

        try:
            df['r_score'] = pd.qcut(df['r_days'], q=5, labels=[5, 4, 3, 2, 1]).astype(int)
        except Exception:
            df['r_score'] = pd.cut(df['r_days'], bins=5, labels=[5, 4, 3, 2, 1]).astype(int)
        try:
            df['f_score'] = pd.qcut(df['f_count'], q=5, labels=[1, 2, 3, 4, 5]).astype(int)
        except Exception:
            df['f_score'] = pd.cut(df['f_count'], bins=5, labels=[1, 2, 3, 4, 5]).astype(int)
        try:
            df['m_score'] = pd.qcut(df['m_amount'], q=5, labels=[1, 2, 3, 4, 5]).astype(int)
        except Exception:
            df['m_score'] = pd.cut(df['m_amount'], bins=5, labels=[1, 2, 3, 4, 5]).astype(int)

        df['rfm_score'] = df['r_score'] + df['f_score'] + df['m_score']

        def classify_rfm(s):
            if s >= 12:
                return '高价值客户'
            elif s >= 8:
                return '中价值客户'
            elif s >= 4:
                return '低价值客户'
            else:
                return '流失客户'

        df['segment'] = df['rfm_score'].apply(classify_rfm)

        # Legacy V1 persistence is disabled; the V2 override below uses task_id.
        return "旧版 RFM 写入已禁用，请使用 V2 任务留存。"

        # Insert new data
        for _, row in df.iterrows():
            execute("""
                INSERT INTO rfm_scores (user_id, username, r_days, f_count, m_amount,
                    r_score, f_score, m_score, rfm_score, segment_label)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (int(row['user_id']), str(row['username']), int(row['r_days']), int(row['f_count']),
                  float(row['m_amount']), int(row['r_score']), int(row['f_score']), int(row['m_score']),
                  int(row['rfm_score']), str(row['segment'])))

        return f"✅ 保存成功！共 {len(df)} 条记录写入 rfm_scores 表"
    except Exception as e:
        return f"❌ 保存失败: {str(e)}"


# ==================== 用户分群 ====================

def user_segmentation():
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import silhouette_score
    except ImportError:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, 'scikit-learn 未安装\n请运行: pip install scikit-learn', ha='center', va='center',
                fontsize=14, color=C_RED, transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['用户', '聚类', '分类']), "scikit-learn 未安装"

    rows = query("""
        SELECT s.user_id, u.username,
               ROUND(AVG(s.amount), 2) AS avg_amount,
               SUM(s.amount) AS total_amount,
               COUNT(*) AS transaction_count,
               COUNT(DISTINCT s.spend_date) AS active_days,
               COUNT(DISTINCT s.category_id) AS category_diversity
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY s.user_id, u.username
    """)

    if not rows or len(rows) < 3:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, '用户数据不足（至少3个用户）', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['用户', '聚类', '分类']), "用户数据不足"

    df = pd.DataFrame([(r['user_id'], r['username'], float(r['avg_amount']), float(r['total_amount']),
                         int(r['transaction_count']), int(r['active_days']), int(r['category_diversity']))
                        for r in rows],
                       columns=['user_id', 'username', 'avg_amount', 'total_amount', 'transaction_count',
                                'active_days', 'category_diversity'])

    features = ['avg_amount', 'total_amount', 'transaction_count', 'active_days', 'category_diversity']
    X = df[features].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Find best k using silhouette_score
    best_k = 3
    best_score = -1
    for k in range(3, min(6, len(df))):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X_scaled)
        score = silhouette_score(X_scaled, labels)
        if score > best_score:
            best_score = score
            best_k = k

    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    df['cluster'] = km.fit_predict(X_scaled)

    # Classify clusters
    cluster_stats = df.groupby('cluster').agg(
        avg_amount=('avg_amount', 'mean'),
        transaction_count=('transaction_count', 'mean')
    ).reset_index()

    highest_amount_cluster = cluster_stats.loc[cluster_stats['avg_amount'].idxmax(), 'cluster']
    highest_freq_cluster = cluster_stats.loc[cluster_stats['transaction_count'].idxmax(), 'cluster']

    def label_cluster(c):
        if c == highest_amount_cluster:
            return '高消费群'
        elif c == highest_freq_cluster and c != highest_amount_cluster:
            return '高频消费群'
        else:
            return '普通消费群'

    df['segment_label'] = df['cluster'].apply(label_cluster)

    cluster_colors_cfg = {0: C_BLUE, 1: C_GREEN, 2: C_ORANGE, 3: C_PURPLE, 4: C_RED}

    # Scatter plot
    fig, ax = plt.subplots(figsize=(10, 7))
    for c in sorted(df['cluster'].unique()):
        subset = df[df['cluster'] == c]
        ax.scatter(subset['avg_amount'], subset['transaction_count'],
                   c=cluster_colors_cfg.get(c, '#888'), s=100, alpha=0.8,
                   label=f"聚类{c} - {subset['segment_label'].iloc[0]}",
                   edgecolors='white', linewidth=0.5)
        for _, row_pt in subset.iterrows():
            ax.annotate(row_pt['username'], (row_pt['avg_amount'], row_pt['transaction_count']),
                        fontsize=7, color='#ccc', xytext=(3, 3), textcoords='offset points')

    ax.set_xlabel('平均消费金额 (元)')
    ax.set_ylabel('交易笔数')
    ax.set_title(f'用户分群 (KMeans k={best_k}, 轮廓系数={best_score:.3f})')
    ax.legend(loc='upper right', facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG)
    dark_style(fig, ax)
    plt.tight_layout()

    # Summary table
    summary_rows = []
    for _, sr in cluster_stats.iterrows():
        c = sr['cluster']
        seg_label = df[df['cluster'] == c]['segment_label'].iloc[0]
        n_users = len(df[df['cluster'] == c])
        summary_rows.append({
            '聚类编号': int(c),
            '用户分类': seg_label,
            '用户数': n_users,
            '平均单笔金额': f"¥{sr['avg_amount']:,.2f}",
            '平均交易笔数': f"{sr['transaction_count']:.1f}"
        })
    summary_df = pd.DataFrame(summary_rows)
    msg = f"最佳聚类数 k={best_k} | 轮廓系数={best_score:.3f}"
    return fig, summary_df, msg


def user_segmentation_save():
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import silhouette_score
    except ImportError:
        return "❌ scikit-learn 未安装"

    rows = query("""
        SELECT s.user_id, u.username,
               ROUND(AVG(s.amount), 2) AS avg_amount,
               SUM(s.amount) AS total_amount,
               COUNT(*) AS transaction_count,
               COUNT(DISTINCT s.spend_date) AS active_days,
               COUNT(DISTINCT s.category_id) AS category_diversity
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY s.user_id, u.username
    """)

    if not rows or len(rows) < 3:
        return "❌ 用户数据不足"

    df = pd.DataFrame([(r['user_id'], r['username'], float(r['avg_amount']), float(r['total_amount']),
                         int(r['transaction_count']), int(r['active_days']), int(r['category_diversity']))
                        for r in rows],
                       columns=['user_id', 'username', 'avg_amount', 'total_amount', 'transaction_count',
                                'active_days', 'category_diversity'])

    features = ['avg_amount', 'total_amount', 'transaction_count', 'active_days', 'category_diversity']
    X = df[features].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    best_k = 3
    best_score = -1
    for k in range(3, min(6, len(df))):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X_scaled)
        score = silhouette_score(X_scaled, labels)
        if score > best_score:
            best_score = score
            best_k = k

    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    df['cluster'] = km.fit_predict(X_scaled)

    cluster_stats = df.groupby('cluster').agg(
        avg_amount=('avg_amount', 'mean'),
        transaction_count=('transaction_count', 'mean')
    ).reset_index()

    highest_amount_cluster = cluster_stats.loc[cluster_stats['avg_amount'].idxmax(), 'cluster']
    highest_freq_cluster = cluster_stats.loc[cluster_stats['transaction_count'].idxmax(), 'cluster']

    def label_cluster(c):
        if c == highest_amount_cluster:
            return '高消费群'
        elif c == highest_freq_cluster and c != highest_amount_cluster:
            return '高频消费群'
        else:
            return '普通消费群'

    df['segment_label'] = df['cluster'].apply(label_cluster)

    # Legacy V1 persistence is disabled; the V2 override below uses task_id.
    return "旧版聚类写入已禁用，请使用 V2 任务留存。"
    for _, row in df.iterrows():
        execute("""
            INSERT INTO user_segments (user_id, username, avg_amount, total_amount, transaction_count,
                active_days, category_diversity, cluster_id, cluster_label)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (int(row['user_id']), str(row['username']), float(row['avg_amount']), float(row['total_amount']),
              int(row['transaction_count']), int(row['active_days']), int(row['category_diversity']),
              int(row['cluster']), str(row['segment_label'])))

    return f"✅ 保存成功！共 {len(df)} 条记录写入 user_segments 表"


# ==================== 流失预测 ====================

def churn_prediction():
    rows = query("""
        SELECT s.user_id, u.username,
               TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
               SUM(s.amount) AS total
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY s.user_id, u.username, DATE_TRUNC('month', s.spend_date)
        ORDER BY s.user_id, month
    """)

    if not rows:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, '暂无消费数据', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        ax2.text(0.5, 0.5, '暂无数据', ha='center', va='center', fontsize=14, color='#aaa', transform=ax2.transAxes)
        dark_style(fig2, ax2)
        plt.tight_layout()
        return fig, fig2, pd.DataFrame(columns=['用户', '流失概率', '风险等级']), "暂无数据"

    user_monthly = {}
    for r in rows:
        uid = r['user_id']
        uname = r['username']
        if uid not in user_monthly:
            user_monthly[uid] = {'username': uname, 'months': {}}
        user_monthly[uid]['months'][r['month']] = float(r['total'])

    results = []
    for uid, data in user_monthly.items():
        months_sorted = sorted(data['months'].keys())
        if len(months_sorted) < 3:
            continue

        monthly_amounts = [data['months'][m] for m in months_sorted]
        last_month = months_sorted[-1]
        last_amount = monthly_amounts[-1]

        # 计算最近12个月
        recent_12 = monthly_amounts[-12:] if len(monthly_amounts) >= 12 else monthly_amounts

        declining_months = 0
        if len(recent_12) >= 3:
            for i in range(3, len(recent_12)):
                recent_3_avg = np.mean(recent_12[max(0, i-3):i])
                older_6_avg = np.mean(recent_12[max(0, i-9):max(0, i-3)]) if i >= 6 else recent_3_avg + 0.01
                if older_6_avg > 0 and recent_3_avg < older_6_avg * 0.5:
                    declining_months += 1

        churn_prob = declining_months / 6.0
        if last_amount == 0:
            churn_prob += 0.3
        churn_prob = min(churn_prob, 1.0)

        if churn_prob >= 0.7:
            risk = '高风险'
        elif churn_prob >= 0.3:
            risk = '中风险'
        else:
            risk = '低风险'

        results.append({
            'user_id': uid,
            'username': data['username'],
            'churn_probability': round(churn_prob, 4),
            'risk_level': risk,
            'declining_months': declining_months,
            'last_month': last_month
        })

    if not results:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, '数据不足以进行流失预测', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        ax2.text(0.5, 0.5, '数据不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax2.transAxes)
        dark_style(fig2, ax2)
        plt.tight_layout()
        return fig, fig2, pd.DataFrame(columns=['用户', '流失概率', '风险等级']), "数据不足"

    results.sort(key=lambda x: x['churn_probability'], reverse=True)

    # Bar chart of churn probability
    fig, ax = plt.subplots(figsize=(12, 6))
    names = [r['username'] for r in results]
    probs = [r['churn_probability'] for r in results]
    bar_colors = [C_RED if p >= 0.7 else C_ORANGE if p >= 0.3 else C_GREEN for p in probs]
    ax.bar(range(len(names)), probs, color=bar_colors)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha='right', fontsize=8)
    ax.set_ylabel('流失概率')
    ax.set_title('用户流失概率')
    ax.axhline(y=0.7, color=C_RED, linestyle='--', alpha=0.5, label='高风险线')
    ax.axhline(y=0.3, color=C_ORANGE, linestyle='--', alpha=0.5, label='中风险线')
    ax.legend(loc='upper right', facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG)
    dark_style(fig, ax)
    plt.tight_layout()

    # Risk distribution pie
    fig2, ax2 = plt.subplots(figsize=(10, 5))
    risk_counts = {'高风险': 0, '中风险': 0, '低风险': 0}
    for r in results:
        risk_counts[r['risk_level']] += 1
    pie_labels = [f"{k}\n({v}人)" for k, v in risk_counts.items() if v > 0]
    pie_vals = [v for v in risk_counts.values() if v > 0]
    if pie_vals:
        ax2.pie(pie_vals, labels=pie_labels, colors=[C_RED, C_ORANGE, C_GREEN][:len(pie_vals)],
                autopct='%1.1f%%', textprops={'color': FG})
    ax2.set_title('流失风险分布')
    dark_style(fig2, ax2)
    plt.tight_layout()

    table_df = pd.DataFrame([(r['username'], r['churn_probability'], r['risk_level'],
                               r['declining_months']) for r in results],
                             columns=['用户', '流失概率', '风险等级', '下降月数'])

    msg = f"流失预测完成 | 高风险: {risk_counts['高风险']}人 | 中风险: {risk_counts['中风险']}人 | 低风险: {risk_counts['低风险']}人"
    return fig, fig2, table_df, msg


def churn_save():
    try:
        rows = query("""
            SELECT s.user_id, u.username,
                   TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
                   SUM(s.amount) AS total
            FROM spending_record s
            JOIN users u ON s.user_id = u.id
            GROUP BY s.user_id, u.username, DATE_TRUNC('month', s.spend_date)
            ORDER BY s.user_id, month
        """)
        if not rows:
            return "❌ 没有数据可保存"

        user_monthly = {}
        for r in rows:
            uid = r['user_id']
            uname = r['username']
            if uid not in user_monthly:
                user_monthly[uid] = {'username': uname, 'months': {}}
            user_monthly[uid]['months'][r['month']] = float(r['total'])

        results = []
        for uid, data in user_monthly.items():
            months_sorted = sorted(data['months'].keys())
            if len(months_sorted) < 3:
                continue
            monthly_amounts = [data['months'][m] for m in months_sorted]
            last_amount = monthly_amounts[-1]
            last_month = months_sorted[-1]
            recent_12 = monthly_amounts[-12:] if len(monthly_amounts) >= 12 else monthly_amounts
            declining_months = 0
            if len(recent_12) >= 3:
                for i in range(3, len(recent_12)):
                    recent_3_avg = np.mean(recent_12[max(0, i-3):i])
                    older_6_avg = np.mean(recent_12[max(0, i-9):max(0, i-3)]) if i >= 6 else recent_3_avg + 0.01
                    if older_6_avg > 0 and recent_3_avg < older_6_avg * 0.5:
                        declining_months += 1
            churn_prob = declining_months / 6.0
            if last_amount == 0:
                churn_prob += 0.3
            churn_prob = min(churn_prob, 1.0)
            risk = '高风险' if churn_prob >= 0.7 else '中风险' if churn_prob >= 0.3 else '低风险'
            results.append((uid, data['username'], churn_prob, risk, declining_months, last_month))

        # Legacy V1 persistence is disabled; the V2 override below uses task_id.
        return "旧版流失写入已禁用，请使用 V2 任务留存。"
        for r in results:
            execute("""
                INSERT INTO churn_predictions (user_id, username, churn_probability, risk_level,
                    declining_months, last_active_date)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, r)
        return f"✅ 保存成功！共 {len(results)} 条记录写入 churn_predictions 表"
    except Exception as e:
        return f"❌ 保存失败: {str(e)}"


# ==================== 金额预测 ====================

def amount_prediction():
    try:
        from sklearn.linear_model import LinearRegression
    except ImportError:
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.text(0.5, 0.5, 'scikit-learn 未安装', ha='center', va='center',
                fontsize=14, color=C_RED, transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['用户', '预测月份', '预测金额', '下限', '上限']), "scikit-learn 未安装"

    rows = query("""
        SELECT u.username,
               TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
               SUM(s.amount) AS total
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY u.username, DATE_TRUNC('month', s.spend_date)
        ORDER BY u.username, month
    """)

    if not rows:
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.text(0.5, 0.5, '暂无消费数据', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['用户', '预测月份', '预测金额', '下限', '上限']), "暂无数据"

    user_data = {}
    for r in rows:
        uname = r['username']
        if uname not in user_data:
            user_data[uname] = []
        user_data[uname].append((r['month'], float(r['total'])))

    fig, ax = plt.subplots(figsize=(16, 8))
    color_idx = 0
    all_predictions = []

    for uname, monthly in user_data.items():
        if len(monthly) < 4:
            continue
        monthly.sort(key=lambda x: x[0])
        amounts = np.array([m[1] for m in monthly])
        month_indices = np.arange(len(amounts))

        # Build features: month number + seasonal + lag
        X = []
        y = []
        for i in range(3, len(amounts)):
            feat = [month_indices[i],
                    np.sin(2 * np.pi * month_indices[i] / 12),
                    np.cos(2 * np.pi * month_indices[i] / 12),
                    amounts[i-1], amounts[i-2], amounts[i-3]]
            X.append(feat)
            y.append(amounts[i])

        if len(X) < 3:
            continue

        model = LinearRegression()
        model.fit(X, y)
        residuals = y - model.predict(X)
        residual_std = np.std(residuals) if len(residuals) > 1 else 0

        # Predict next 3 months
        pred_amounts = []
        pred_lower = []
        pred_upper = []
        last_3 = list(amounts[-3:])
        last_month_str = monthly[-1][0]

        for step in range(1, 4):
            pred_idx = len(amounts) + step - 1
            feat = [pred_idx,
                    np.sin(2 * np.pi * pred_idx / 12),
                    np.cos(2 * np.pi * pred_idx / 12),
                    last_3[-1], last_3[-2], last_3[-3]]
            pred = model.predict([feat])[0]
            pred = max(0, pred)
            pred_amounts.append(pred)
            pred_lower.append(max(0, pred - 1.96 * residual_std))
            pred_upper.append(pred + 1.96 * residual_std)
            last_3.append(pred)
            last_3.pop(0)

            # Parse last_month_str and add step months
            yr, mo = last_month_str.split('-')
            mo_int = int(mo) + step
            yr_int = int(yr) + (mo_int - 1) // 12
            mo_int = ((mo_int - 1) % 12) + 1
            pred_month_str = f"{yr_int}-{mo_int:02d}"

            all_predictions.append({
                '用户': uname,
                '预测月份': pred_month_str,
                '预测金额': round(pred, 2),
                '下限': round(max(0, pred - 1.96 * residual_std), 2),
                '上限': round(pred + 1.96 * residual_std, 2)
            })

        # Plot history + prediction
        clr = COLORS[color_idx % len(COLORS)]
        color_idx += 1
        hist_months = [m[0] for m in monthly]
        ax.plot(range(len(amounts)), amounts, color=clr, linewidth=1.8, marker='o', markersize=3, label=uname)
        pred_x = range(len(amounts), len(amounts) + 3)
        ax.plot(pred_x, pred_amounts, color=clr, linewidth=1.8, linestyle='--', marker='s', markersize=3)
        ax.fill_between(pred_x, pred_lower, pred_upper, color=clr, alpha=0.08)

    if color_idx == 0:
        ax.text(0.5, 0.5, '数据不足以进行预测（每个用户至少需要4个月数据）', ha='center', va='center',
                fontsize=14, color='#aaa', transform=ax.transAxes)

    ax.set_xlabel('月份序号')
    ax.set_ylabel('消费金额 (元)')
    ax.set_title('金额预测 - 未来3个月 (实线=历史, 虚线=预测, 阴影=95%置信区间)')
    ax.legend(loc='upper left', fontsize=7, facecolor='#2a2a2a', edgecolor='#444', labelcolor=FG, ncol=2)
    dark_style(fig, ax)
    plt.tight_layout()

    pred_df = pd.DataFrame(all_predictions) if all_predictions else pd.DataFrame(columns=['用户', '预测月份', '预测金额', '下限', '上限'])
    msg = f"金额预测完成 | 涉及 {color_idx} 个用户 | 共 {len(all_predictions)} 条预测"
    return fig, pred_df, msg


def amount_prediction_save():
    try:
        from sklearn.linear_model import LinearRegression
    except ImportError:
        return "❌ scikit-learn 未安装"

    rows = query("""
        SELECT s.user_id, u.username,
               TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
               SUM(s.amount) AS total
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        GROUP BY s.user_id, u.username, DATE_TRUNC('month', s.spend_date)
        ORDER BY s.user_id, u.username, month
    """)
    if not rows:
        return "❌ 没有数据可保存"

    user_data = {}
    for r in rows:
        uid = r['user_id']
        uname = r['username']
        if uid not in user_data:
            user_data[uid] = {'username': uname, 'months': {}}
        user_data[uid]['months'][r['month']] = float(r['total'])

    all_preds = []
    for uid, data in user_data.items():
        monthly = sorted(data['months'].items(), key=lambda x: x[0])
        if len(monthly) < 4:
            continue
        amounts = np.array([m[1] for m in monthly])
        month_indices = np.arange(len(amounts))
        X, y = [], []
        for i in range(3, len(amounts)):
            feat = [month_indices[i],
                    np.sin(2 * np.pi * month_indices[i] / 12),
                    np.cos(2 * np.pi * month_indices[i] / 12),
                    amounts[i-1], amounts[i-2], amounts[i-3]]
            X.append(feat)
            y.append(amounts[i])
        if len(X) < 3:
            continue
        model = LinearRegression()
        model.fit(X, y)
        residuals = y - model.predict(X)
        residual_std = np.std(residuals) if len(residuals) > 1 else 0
        last_3 = list(amounts[-3:])
        last_month_str = monthly[-1][0]
        for step in range(1, 4):
            pred_idx = len(amounts) + step - 1
            feat = [pred_idx,
                    np.sin(2 * np.pi * pred_idx / 12),
                    np.cos(2 * np.pi * pred_idx / 12),
                    last_3[-1], last_3[-2], last_3[-3]]
            pred = model.predict([feat])[0]
            pred = max(0, pred)
            yr, mo = last_month_str.split('-')
            mo_int = int(mo) + step
            yr_int = int(yr) + (mo_int - 1) // 12
            mo_int = ((mo_int - 1) % 12) + 1
            pred_month_str = f"{yr_int}-{mo_int:02d}"
            all_preds.append((int(uid), str(data['username']), pred_month_str,
                              float(pred), float(max(0, pred - 1.96 * residual_std)),
                              float(pred + 1.96 * residual_std)))
            last_3.append(pred)
            last_3.pop(0)

    return "旧版金额预测写入已禁用。"
    for p in all_preds:
        execute("""
            INSERT INTO amount_predictions (user_id, username, predict_month,
                predicted_amount, lower_bound, upper_bound)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, p)
    return f"✅ 保存成功！共 {len(all_preds)} 条记录写入 amount_predictions 表"


# ==================== 销量预测 ====================

def sales_prediction():
    rows = query("""
        SELECT TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
               COALESCE(sc.parent_category, '其他') AS category,
               COALESCE(m.merchant_name, '未知商户') AS merchant,
               SUM(s.amount) AS total
        FROM spending_record s
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        GROUP BY DATE_TRUNC('month', s.spend_date), sc.parent_category, m.merchant_name
        ORDER BY month
    """)

    if not rows:
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.text(0.5, 0.5, '暂无消费数据', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['预测月份', '项目', '类型', '预测销量']), "暂无数据"

    # Aggregate by category monthly
    cat_monthly = {}
    merchant_monthly = {}
    for r in rows:
        month = r['month']
        cat = r['category']
        merchant = r['merchant']
        amt = float(r['total'])
        cat_monthly.setdefault(cat, {})[month] = cat_monthly.get(cat, {}).get(month, 0) + amt
        merchant_monthly.setdefault(merchant, {})[month] = merchant_monthly.get(merchant, {}).get(month, 0) + amt

    predictions = []

    def simple_exp_smooth(series, alpha=0.3, periods=3):
        if len(series) < 2:
            return [series[-1]] * periods
        smoothed = series[-1]
        forecasts = []
        for _ in range(periods):
            forecasts.append(smoothed)
        return forecasts

    # Predict by category
    all_months = sorted(set(r['month'] for r in rows))
    if not all_months:
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.text(0.5, 0.5, '数据不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['预测月份', '项目', '类型', '预测销量']), "数据不足"

    last_month = all_months[-1]
    yr, mo = last_month.split('-')
    future_months = []
    for i in range(1, 4):
        mo_int = int(mo) + i
        yr_int = int(yr) + (mo_int - 1) // 12
        mo_int = ((mo_int - 1) % 12) + 1
        future_months.append(f"{yr_int}-{mo_int:02d}")

    cat_preds = {}
    for cat, monthly in cat_monthly.items():
        sorted_vals = [monthly.get(m, 0) for m in sorted(monthly.keys())]
        if len(sorted_vals) < 2:
            continue
        forecasts = simple_exp_smooth(sorted_vals, 0.3, 3)
        cat_preds[cat] = forecasts

    merchant_preds = {}
    for merchant, monthly in merchant_monthly.items():
        sorted_vals = [monthly.get(m, 0) for m in sorted(monthly.keys())]
        if len(sorted_vals) < 2:
            continue
        forecasts = simple_exp_smooth(sorted_vals, 0.3, 3)
        merchant_preds[merchant] = forecasts

    # Bar chart: categories predicted sales
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    if cat_preds:
        top_cats = sorted(cat_preds.items(), key=lambda x: sum(x[1]), reverse=True)[:8]
        cat_names = [c[0] for c in top_cats]
        cat_vals = [sum(c[1]) for c in top_cats]
        bars1 = ax1.bar(range(len(cat_names)), cat_vals, color=COLORS[:len(cat_names)])
        ax1.set_xticks(range(len(cat_names)))
        ax1.set_xticklabels(cat_names, rotation=30, ha='right', fontsize=8)
        ax1.set_ylabel('预测销售额 (元)')
        ax1.set_title('品类销量预测 (未来3月合计)')
        for bar, val in zip(bars1, cat_vals):
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(cat_vals)*0.01,
                     f'¥{val:,.0f}', ha='center', fontsize=7, color='#aaa')
    else:
        ax1.text(0.5, 0.5, '品类数据不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax1.transAxes)
    dark_style(fig, ax1)

    if merchant_preds:
        top_merchants = sorted(merchant_preds.items(), key=lambda x: sum(x[1]), reverse=True)[:8]
        m_names = [m[0] for m in top_merchants]
        m_vals = [sum(m[1]) for m in top_merchants]
        bars2 = ax2.bar(range(len(m_names)), m_vals, color=COLORS[:len(m_names)])
        ax2.set_xticks(range(len(m_names)))
        ax2.set_xticklabels(m_names, rotation=30, ha='right', fontsize=8)
        ax2.set_ylabel('预测销售额 (元)')
        ax2.set_title('商户销量预测 (未来3月合计)')
        for bar, val in zip(bars2, m_vals):
            ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(m_vals)*0.01,
                     f'¥{val:,.0f}', ha='center', fontsize=7, color='#aaa')
    else:
        ax2.text(0.5, 0.5, '商户数据不足', ha='center', va='center', fontsize=14, color='#aaa', transform=ax2.transAxes)
    dark_style(fig, ax2)
    plt.tight_layout()

    # Build predictions table
    for i, fm in enumerate(future_months):
        for cat, forecasts in cat_preds.items():
            predictions.append({
                '预测月份': fm,
                '项目': cat,
                '类型': '品类',
                '预测销量': round(forecasts[i], 2)
            })
        for merchant, forecasts in merchant_preds.items():
            predictions.append({
                '预测月份': fm,
                '项目': merchant,
                '类型': '商户',
                '预测销量': round(forecasts[i], 2)
            })

    pred_df = pd.DataFrame(predictions)
    msg = f"销量预测完成 | 品类: {len(cat_preds)} 个 | 商户: {len(merchant_preds)} 个"
    return fig, pred_df, msg


def sales_prediction_save():
    try:
        rows = query("""
            SELECT TO_CHAR(DATE_TRUNC('month', s.spend_date)::DATE, 'YYYY-MM') AS month,
                   COALESCE(sc.parent_category, '其他') AS category,
                   COALESCE(m.merchant_name, '未知商户') AS merchant,
                   SUM(s.amount) AS total
            FROM spending_record s
            LEFT JOIN spending_category sc ON s.category_id = sc.category_id
            LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
            GROUP BY DATE_TRUNC('month', s.spend_date), sc.parent_category, m.merchant_name
            ORDER BY month
        """)
        if not rows:
            return "❌ 没有数据可保存"

        all_months = sorted(set(r['month'] for r in rows))
        if not all_months:
            return "❌ 数据不足"
        last_month = all_months[-1]
        yr, mo = last_month.split('-')
        future_months = []
        for i in range(1, 4):
            mo_int = int(mo) + i
            yr_int = int(yr) + (mo_int - 1) // 12
            mo_int = ((mo_int - 1) % 12) + 1
            future_months.append(f"{yr_int}-{mo_int:02d}")

        cat_monthly = {}
        merchant_monthly = {}
        for r in rows:
            month = r['month']
            cat = r['category']
            merchant = r['merchant']
            amt = float(r['total'])
            cat_monthly.setdefault(cat, {})[month] = cat_monthly.get(cat, {}).get(month, 0) + amt
            merchant_monthly.setdefault(merchant, {})[month] = merchant_monthly.get(merchant, {}).get(month, 0) + amt

        def simple_exp_smooth(series):
            return [series[-1]] * 3 if len(series) > 0 else [0] * 3

        save_rows = []
        for i, fm in enumerate(future_months):
            for cat, monthly in cat_monthly.items():
                sorted_vals = [monthly.get(m, 0) for m in sorted(monthly.keys())]
                forecasts = simple_exp_smooth(sorted_vals)
                save_rows.append((fm, cat, 'category', float(forecasts[i])))
            for merchant, monthly in merchant_monthly.items():
                sorted_vals = [monthly.get(m, 0) for m in sorted(monthly.keys())]
                forecasts = simple_exp_smooth(sorted_vals)
                save_rows.append((fm, merchant, 'merchant', float(forecasts[i])))

        return "旧版销售额预测写入已禁用。"
        for sr in save_rows:
            execute("""
                INSERT INTO sales_predictions (predict_month, item_name, item_type, predicted_sales)
                VALUES (%s, %s, %s, %s)
            """, sr)
        return f"✅ 保存成功！共 {len(save_rows)} 条记录写入 sales_predictions 表"
    except Exception as e:
        return f"❌ 保存失败: {str(e)}"


# ==================== 推荐分析 ====================

def recommendation_analysis():
    rows = query("""
        SELECT s.user_id, u.username,
               COALESCE(sc.parent_category, '其他') AS category,
               COALESCE(m.merchant_name, '未知商户') AS merchant,
               SUM(s.amount) AS total,
               COUNT(*) AS cnt
        FROM spending_record s
        JOIN users u ON s.user_id = u.id
        LEFT JOIN spending_category sc ON s.category_id = sc.category_id
        LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
        GROUP BY s.user_id, u.username, sc.parent_category, m.merchant_name
        ORDER BY s.user_id
    """)

    if not rows or len(set(r['user_id'] for r in rows)) < 2:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, '用户数据不足（至少需要2个用户）', ha='center', va='center',
                fontsize=14, color='#aaa', transform=ax.transAxes)
        dark_style(fig, ax)
        plt.tight_layout()
        return fig, pd.DataFrame(columns=['用户', '推荐项目', '类型', '推荐得分']), "用户数据不足"

    # Build user-item matrix for categories
    user_cat = {}
    user_merchant = {}
    for r in rows:
        uid = r['user_id']
        uname = r['username']
        cat = r['category']
        merchant = r['merchant']
        amt = float(r['total'])

        if uid not in user_cat:
            user_cat[uid] = {'username': uname, 'categories': {}}
        user_cat[uid]['categories'][cat] = user_cat[uid]['categories'].get(cat, 0) + amt

        if uid not in user_merchant:
            user_merchant[uid] = {'username': uname, 'merchants': {}}
        user_merchant[uid]['merchants'][merchant] = user_merchant[uid]['merchants'].get(merchant, 0) + amt

    # Collaborative filtering on categories
    user_ids = list(user_cat.keys())
    all_categories = sorted(set(cat for u in user_cat for cat in user_cat[u]['categories']))

    # Build user-category matrix
    cat_matrix = {}
    for uid in user_ids:
        total = sum(user_cat[uid]['categories'].values())
        cat_matrix[uid] = {cat: user_cat[uid]['categories'].get(cat, 0) / total if total > 0 else 0
                           for cat in all_categories}

    # Calculate similarity between users
    recommendations = []
    for uid in user_ids:
        uname = user_cat[uid]['username']
        user_vec = np.array([cat_matrix[uid].get(c, 0) for c in all_categories])

        # Find similar users
        sim_scores = []
        for other_uid in user_ids:
            if other_uid == uid:
                continue
            other_vec = np.array([cat_matrix[other_uid].get(c, 0) for c in all_categories])
            # Cosine similarity
            dot = np.dot(user_vec, other_vec)
            norm_user = np.linalg.norm(user_vec)
            norm_other = np.linalg.norm(other_vec)
            if norm_user > 0 and norm_other > 0:
                sim = dot / (norm_user * norm_other)
                sim_scores.append((other_uid, sim))

        if not sim_scores:
            continue

        sim_scores.sort(key=lambda x: x[1], reverse=True)
        top_similar = sim_scores[:3]

        # Recommend categories the user doesn't use
        user_cats_used = set(user_cat[uid]['categories'].keys())
        cat_scores = {}
        for sim_uid, sim in top_similar:
            for cat, amt in user_cat[sim_uid]['categories'].items():
                if cat not in user_cats_used:
                    score = sim * amt
                    cat_scores[cat] = cat_scores.get(cat, 0) + score

        for cat, score in sorted(cat_scores.items(), key=lambda x: x[1], reverse=True)[:5]:
            recommendations.append({
                '用户': uname,
                '推荐项目': cat,
                '类型': '品类',
                '推荐得分': round(score, 2)
            })

        # Recommend merchants
        user_merchants_used = set(user_merchant[uid]['merchants'].keys())
        merchant_scores = {}
        for sim_uid, sim in top_similar:
            for merch, amt in user_merchant[sim_uid]['merchants'].items():
                if merch not in user_merchants_used:
                    score = sim * amt
                    merchant_scores[merch] = merchant_scores.get(merch, 0) + score

        for merch, score in sorted(merchant_scores.items(), key=lambda x: x[1], reverse=True)[:5]:
            recommendations.append({
                '用户': uname,
                '推荐项目': merch,
                '类型': '商户',
                '推荐得分': round(score, 2)
            })

    # Top 10 overall recommendations for display
    rec_df = pd.DataFrame(recommendations) if recommendations else pd.DataFrame(
        columns=['用户', '推荐项目', '类型', '推荐得分'])
    if not rec_df.empty:
        rec_df = rec_df.sort_values('推荐得分', ascending=False).head(50)

    # Bar chart: top recommendations
    fig, ax = plt.subplots(figsize=(12, 7))
    if not rec_df.empty:
        top_n = rec_df.head(15)
        labels = [f"{r['用户']}-{r['推荐项目']}" for _, r in top_n.iterrows()]
        scores = top_n['推荐得分'].tolist()
        bar_colors = [C_BLUE if r['类型'] == '品类' else C_GREEN for _, r in top_n.iterrows()]
        ax.barh(range(len(labels)), scores, color=bar_colors)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel('推荐得分')
        ax.set_title('Top 推荐结果 (蓝=品类, 绿=商户)')
    else:
        ax.text(0.5, 0.5, '暂无推荐结果', ha='center', va='center',
                fontsize=14, color='#aaa', transform=ax.transAxes)
    dark_style(fig, ax)
    plt.tight_layout()

    msg = f"推荐分析完成 | 共 {len(recommendations)} 条推荐建议"
    return fig, rec_df, msg


def recommendation_save():
    try:
        rows = query("""
            SELECT s.user_id, u.username,
                   COALESCE(sc.parent_category, '其他') AS category,
                   COALESCE(m.merchant_name, '未知商户') AS merchant,
                   SUM(s.amount) AS total
            FROM spending_record s
            JOIN users u ON s.user_id = u.id
            LEFT JOIN spending_category sc ON s.category_id = sc.category_id
            LEFT JOIN merchant m ON s.merchant_id = m.merchant_id
            GROUP BY s.user_id, u.username, sc.parent_category, m.merchant_name
            ORDER BY s.user_id
        """)
        if not rows:
            return "❌ 没有数据可保存"

        user_cat = {}
        user_merchant = {}
        for r in rows:
            uid = r['user_id']
            uname = r['username']
            cat = r['category']
            merchant = r['merchant']
            amt = float(r['total'])
            if uid not in user_cat:
                user_cat[uid] = {'username': uname, 'categories': {}}
            user_cat[uid]['categories'][cat] = user_cat[uid]['categories'].get(cat, 0) + amt
            if uid not in user_merchant:
                user_merchant[uid] = {'username': uname, 'merchants': {}}
            user_merchant[uid]['merchants'][merchant] = user_merchant[uid]['merchants'].get(merchant, 0) + amt

        user_ids = list(user_cat.keys())
        all_categories = sorted(set(cat for u in user_cat for cat in user_cat[u]['categories']))
        cat_matrix = {}
        for uid in user_ids:
            total = sum(user_cat[uid]['categories'].values())
            cat_matrix[uid] = {cat: user_cat[uid]['categories'].get(cat, 0) / total if total > 0 else 0
                               for cat in all_categories}

        all_recs = []
        for uid in user_ids:
            uname = user_cat[uid]['username']
            user_vec = np.array([cat_matrix[uid].get(c, 0) for c in all_categories])
            sim_scores = []
            for other_uid in user_ids:
                if other_uid == uid:
                    continue
                other_vec = np.array([cat_matrix[other_uid].get(c, 0) for c in all_categories])
                dot = np.dot(user_vec, other_vec)
                norm_user = np.linalg.norm(user_vec)
                norm_other = np.linalg.norm(other_vec)
                if norm_user > 0 and norm_other > 0:
                    sim = dot / (norm_user * norm_other)
                    sim_scores.append((other_uid, sim))
            if not sim_scores:
                continue
            sim_scores.sort(key=lambda x: x[1], reverse=True)
            top_similar = sim_scores[:3]
            user_cats_used = set(user_cat[uid]['categories'].keys())
            cat_scores = {}
            for sim_uid, sim in top_similar:
                for c, amt in user_cat[sim_uid]['categories'].items():
                    if c not in user_cats_used:
                        cat_scores[c] = cat_scores.get(c, 0) + sim * amt
            for c, score in sorted(cat_scores.items(), key=lambda x: x[1], reverse=True)[:5]:
                all_recs.append((int(uid), uname, c, 'category', float(score)))
            user_merchants_used = set(user_merchant[uid]['merchants'].keys())
            merchant_scores = {}
            for sim_uid, sim in top_similar:
                for m, amt in user_merchant[sim_uid]['merchants'].items():
                    if m not in user_merchants_used:
                        merchant_scores[m] = merchant_scores.get(m, 0) + sim * amt
            for m, score in sorted(merchant_scores.items(), key=lambda x: x[1], reverse=True)[:5]:
                all_recs.append((int(uid), uname, m, 'merchant', float(score)))

        return "旧版推荐写入已禁用。"
        for r in all_recs:
            execute("""
                INSERT INTO recommendations (user_id, username, recommended_item, item_type, score)
                VALUES (%s, %s, %s, %s, %s)
            """, r)
        return f"✅ 保存成功！共 {len(all_recs)} 条记录写入 recommendations 表"
    except Exception as e:
        return f"❌ 保存失败: {str(e)}"


# ==================== V2 explainable algorithm views ====================

def _empty_plot(message, figsize=(10, 5)):
    fig, ax = plt.subplots(figsize=figsize)
    ax.text(0.5, 0.5, message, ha='center', va='center', fontsize=14,
            color='#cccccc', transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])
    dark_style(fig, ax)
    plt.tight_layout()
    return fig


def _v2_rfm_dataframe(task_id):
    rows = query("""
        SELECT r.customer_id, c.customer_no, c.name,
               r.recency_days AS r_days, r.frequency AS f_count,
               r.monetary AS m_amount, r.r_score, r.f_score, r.m_score,
               r.segment
        FROM ml.rfm_result r
        JOIN biz.customer c ON c.customer_id = r.customer_id
        WHERE r.task_id = %s
        ORDER BY r.customer_id
    """, (task_id,))
    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        return df
    df['r_days'] = df['r_days'].astype(int)
    df['f_count'] = df['f_count'].astype(int)
    df['m_amount'] = df['m_amount'].astype(float)
    for column in ('r_score', 'f_score', 'm_score'):
        df[column] = df[column].astype(int)
    df['rfm_score'] = df[['r_score', 'f_score', 'm_score']].sum(axis=1)
    df['customer'] = df['customer_no'] + ' · ' + df['name']
    return df


def rfm_analysis():
    columns = ['客户', '最近消费(天)', '消费频次', '累计净消费', 'R分', 'F分', 'M分', '客户分层']
    try:
        task_id = _run_v2_task('rfm')
        df = _v2_rfm_dataframe(task_id)
    except Exception as exc:
        empty = _empty_plot('RFM 分析执行失败')
        message = str(exc).replace('\n', ' ')[:300]
        return empty, _empty_plot('暂无分层数据'), pd.DataFrame(columns=columns), f'### 分析失败\n{message}'
    if len(df) < 5:
        empty = _empty_plot('客户数据不足（至少需要 5 个客户）')
        return empty, _empty_plot('暂无分层数据'), pd.DataFrame(columns=columns), '### 暂无结论\n客户数据不足。'

    segment_order = ['高价值客户', '重要保持客户', '新近客户', '一般客户', '流失预警客户']
    colors = {
        '高价值客户': C_GREEN, '重要保持客户': C_BLUE, '新近客户': C_PURPLE,
        '一般客户': C_ORANGE, '流失预警客户': C_RED,
    }

    fig, ax = plt.subplots(figsize=(11, 6.5))
    for name in segment_order:
        subset = df[df['segment'] == name]
        if subset.empty:
            continue
        point_size = np.clip(18 + subset['f_count'] * 3, 20, 130)
        ax.scatter(subset['r_days'], subset['m_amount'], s=point_size,
                   color=colors[name], alpha=0.6, label=f'{name}（{len(subset):,}）',
                   edgecolors='white', linewidth=.25)
    ax.invert_xaxis()
    ax.set_xlabel('最近消费距今天数（越靠右越活跃）')
    ax.set_ylabel('累计净消费金额（元）')
    ax.set_title('客户价值地图：位置看价值，气泡大小看消费频次')
    ax.legend(loc='upper left', facecolor='#252525', edgecolor='#444', labelcolor=FG,
              fontsize=8, ncols=2)
    dark_style(fig, ax)
    plt.tight_layout()

    counts = df['segment'].value_counts().reindex(segment_order, fill_value=0)
    fig_bar, ax_bar = plt.subplots(figsize=(11, 5.2))
    bars = ax_bar.barh(segment_order[::-1], counts.values[::-1],
                       color=[colors[name] for name in segment_order[::-1]])
    for bar, value in zip(bars, counts.values[::-1]):
        pct = value / len(df) * 100
        ax_bar.text(bar.get_width() + max(counts.max(), 1) * .015,
                    bar.get_y() + bar.get_height() / 2,
                    f'{value:,} 人  ·  {pct:.1f}%', va='center', color=FG, fontsize=9)
    ax_bar.set_xlabel('客户数量')
    ax_bar.set_title('客户分层结构：每一层有多少客户')
    dark_style(fig_bar, ax_bar)
    plt.tight_layout()

    table = df.sort_values(['rfm_score', 'm_amount'], ascending=False).head(500)[
        ['customer', 'r_days', 'f_count', 'm_amount', 'r_score', 'f_score', 'm_score', 'segment']
    ].copy()
    table.columns = columns
    table['累计净消费'] = table['累计净消费'].round(2)
    high = int(counts.get('高价值客户', 0))
    warning = int(counts.get('流失预警客户', 0))
    insight = f"""
### 读图结论
- 本次分析已自动保存为任务 **#{task_id}**，图表、明细和数据库结果使用完全相同的分层规则。
- **右上区域**代表最近购买且累计贡献高的客户，优先用于会员权益和复购运营。
- 气泡越大代表消费次数越多；当前共有 **{len(df):,}** 位客户，其中高价值客户 **{high:,}** 位。
- 流失预警客户 **{warning:,}** 位，建议结合最近购买时间安排召回活动。
- 明细表按综合 RFM 得分排序，仅展示优先级最高的 500 位客户。
"""
    return fig, fig_bar, table, insight


def _run_v2_task(task_type):
    from app import create_app
    from app.extensions import db
    from app.services.algorithms import run_churn, run_rfm
    from sqlalchemy import text

    flask_app = create_app()
    with flask_app.app_context():
        operator_id = None
        if task_type == 'rfm':
            return run_rfm(operator_id)
        current_rfm = db.session.execute(text("SELECT COUNT(*) FROM ads.customer_rfm")).scalar_one()
        if current_rfm == 0:
            run_rfm(operator_id)
        if task_type == 'churn':
            return run_churn(operator_id, 90)
        raise ValueError('未知任务类型')


def rfm_save():
    return '执行 RFM 分析时已自动创建 task_id 并留存结果，无需重复保存。'


def _v2_segmentation_dataframe():
    rows = query("""
        WITH tx AS (
            SELECT c.customer_id, c.customer_no, c.name,
                   AVG(f.gross_amount) FILTER (WHERE f.flow_type = 'payment') AS avg_amount,
                   SUM(f.net_amount) AS total_amount,
                   COUNT(DISTINCT f.order_id) FILTER (WHERE f.flow_type = 'payment') AS transaction_count,
                   COUNT(DISTINCT date_trunc('month', f.occurred_at))
                       FILTER (WHERE f.flow_type = 'payment') AS active_months
            FROM biz.customer c
            JOIN dwd.consumption_flow f ON f.customer_id = c.customer_id
            GROUP BY c.customer_id, c.customer_no, c.name
        ), category_feature AS (
            SELECT o.customer_id, COUNT(DISTINCT p.category_id) AS category_diversity
            FROM biz.sales_order o
            JOIN biz.order_item i ON i.order_id = o.order_id
            JOIN biz.product p ON p.product_id = i.product_id
            GROUP BY o.customer_id
        )
        SELECT tx.*, COALESCE(cf.category_diversity, 0) AS category_diversity
        FROM tx LEFT JOIN category_feature cf ON cf.customer_id = tx.customer_id
        WHERE tx.transaction_count > 0
        ORDER BY tx.customer_id
    """)
    df = pd.DataFrame([dict(row) for row in rows])
    if not df.empty:
        for column in ['avg_amount', 'total_amount', 'transaction_count', 'active_months', 'category_diversity']:
            df[column] = df[column].astype(float)
        df['customer'] = df['customer_no'] + ' · ' + df['name']
    return df


def _save_cluster_task(df, cluster_count, silhouette, features, minimum_cluster_size):
    from psycopg2.extras import execute_values

    connection = _db_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO ml.model_task
                        (task_type, status, parameters, started_at, finished_at)
                    VALUES ('kmeans', 'success', %s::jsonb, now(), now())
                    RETURNING task_id
                """, (json.dumps({
                    'source': 'gradio', 'clusters': int(cluster_count), 'features': features,
                    'log_features': [name for name in features if name in ('avg_amount', 'total_amount')],
                    'minimum_cluster_size': int(minimum_cluster_size),
                    'random_state': 42, 'n_init': 20,
                }),))
                task_id = cursor.fetchone()['task_id']
                execute_values(cursor, """
                    INSERT INTO ml.cluster_result
                        (task_id, customer_id, cluster_label, distance)
                    VALUES %s
                """, [(
                    task_id, int(row.customer_id), int(row.cluster), float(row.distance),
                ) for row in df.itertuples()], page_size=1000)
                execute_values(cursor, """
                    INSERT INTO ml.model_metric
                        (task_id, metric_name, metric_value, dataset)
                    VALUES %s
                """, [
                    (task_id, 'silhouette', float(silhouette), 'all'),
                    (task_id, 'customer_count', float(len(df)), 'all'),
                    (task_id, 'cluster_count', float(cluster_count), 'all'),
                ])
        return task_id
    finally:
        connection.close()


def user_segmentation():
    try:
        from sklearn.preprocessing import StandardScaler
        from app.services.clustering import choose_stable_kmeans, select_model_features
    except ImportError:
        return _empty_plot('scikit-learn 未安装'), pd.DataFrame(), '### 无法分析\n缺少 scikit-learn。'

    df = _v2_segmentation_dataframe()
    if len(df) < 10:
        return _empty_plot('有效消费客户不足（至少 10 位）'), pd.DataFrame(), '### 暂无结论\n有效客户不足。'

    features = ['avg_amount', 'total_amount', 'transaction_count', 'active_months', 'category_diversity']
    model_features = select_model_features(df, features)
    if len(model_features) < 2:
        message = '有效聚类特征不足（至少需要 2 项有明显差异的指标）'
        return _empty_plot(message), pd.DataFrame(), f'### 暂无聚类结论\n{message}，请补充更多差异化消费数据。'
    model_frame = df[model_features].copy()
    for feature in ('avg_amount', 'total_amount'):
        if feature in model_frame:
            model_frame[feature] = np.log1p(model_frame[feature])
    scaler = StandardScaler()
    scaled = scaler.fit_transform(model_frame)
    minimum_cluster_size = max(2, int(np.ceil(len(df) * .02)))
    selection = choose_stable_kmeans(scaled)
    if selection is None:
        message = f'没有满足最小群体规模（{minimum_cluster_size} 位）的有效聚类'
        return _empty_plot(message), pd.DataFrame(), f'### 暂无聚类结论\n{message}，请补充更多差异化消费数据。'

    best_score = selection.score
    best_k = selection.clusters
    df['cluster'] = selection.labels
    df['distance'] = selection.distances
    stats = df.groupby('cluster').agg(
        customer_count=('customer_id', 'count'), avg_amount=('avg_amount', 'mean'),
        total_amount=('total_amount', 'mean'), transaction_count=('transaction_count', 'mean'),
        active_months=('active_months', 'mean'), category_diversity=('category_diversity', 'mean'),
    ).reset_index()
    ranked = stats.sort_values('total_amount', ascending=False)['cluster'].tolist()
    labels = {}
    names = ['高价值群', '稳定消费群', '潜力成长群', '基础消费群', '低活跃群']
    for index, cluster in enumerate(ranked):
        labels[int(cluster)] = names[min(index, len(names) - 1)]
    df['segment'] = df['cluster'].map(labels)
    task_id = _save_cluster_task(
        df, best_k, best_score, model_features, minimum_cluster_size,
    )
    palette = [C_GREEN, C_BLUE, C_PURPLE, C_ORANGE, C_RED]

    fig, (ax_scatter, ax_profile) = plt.subplots(1, 2, figsize=(16, 6.8), gridspec_kw={'width_ratios': [1.15, 1]})
    sample = df.sample(min(3000, len(df)), random_state=42)
    for cluster in sorted(df['cluster'].unique()):
        subset = sample[sample['cluster'] == cluster]
        ax_scatter.scatter(subset['avg_amount'], subset['total_amount'],
                           s=np.clip(subset['active_months'] * 6, 18, 100),
                           color=palette[int(cluster) % len(palette)], alpha=.55,
                           label=f"{labels[int(cluster)]}（{len(df[df['cluster'] == cluster]):,}）",
                           edgecolors='white', linewidth=.2)
    ax_scatter.set_xlabel('平均客单价（元）')
    ax_scatter.set_ylabel('人均累计净消费（元）')
    ax_scatter.set_title('客户群位置：横向看客单价，纵向看累计价值')
    ax_scatter.legend(facecolor='#252525', edgecolor='#444', labelcolor=FG, fontsize=8)
    dark_style(fig, ax_scatter)

    profile = stats.set_index('cluster')[features].copy()
    overall = df[features].mean().replace(0, np.nan)
    relative = ((profile / overall) - 1).fillna(0) * 100
    heatmap_values = relative.clip(-60, 60)
    image = ax_profile.imshow(heatmap_values.values, cmap='RdYlGn', vmin=-60, vmax=60, aspect='auto')
    ax_profile.set_xticks(range(len(features)))
    ax_profile.set_xticklabels(['客单价', '累计消费', '消费频次', '活跃月数', '品类广度'], rotation=25, ha='right')
    ax_profile.set_yticks(range(len(relative)))
    ax_profile.set_yticklabels([labels[int(c)] for c in relative.index])
    for row_index in range(relative.shape[0]):
        for column_index in range(relative.shape[1]):
            value = relative.iloc[row_index, column_index]
            ax_profile.text(column_index, row_index, f'{value:+.0f}%', ha='center', va='center',
                            color='white' if abs(value) > 32 else '#111111', fontsize=8)
    ax_profile.set_title('群体画像热力图：相对全体客户平均水平', color=FG)
    ax_profile.set_facecolor('#252525')
    ax_profile.tick_params(colors='#bbbbbb')
    for spine in ax_profile.spines.values():
        spine.set_color('#444444')
    colorbar = fig.colorbar(image, ax=ax_profile, fraction=.045, pad=.03)
    colorbar.set_label('相对平均值（%）', color='#bbbbbb')
    colorbar.ax.tick_params(colors='#bbbbbb')
    colorbar.outline.set_edgecolor('#444444')
    fig.patch.set_facecolor(BG)
    plt.tight_layout()

    actions = {'高价值群': '重点维护与专属权益', '稳定消费群': '推动连带购买',
               '潜力成长群': '使用阶梯优惠提升频次', '基础消费群': '首购转复购培育',
               '低活跃群': '低成本触达或召回'}
    summary_rows = []
    for _, row in stats.sort_values('total_amount', ascending=False).iterrows():
        label = labels[int(row['cluster'])]
        summary_rows.append({
            '客户群': label, '客户数': int(row['customer_count']),
            '客户占比': f"{row['customer_count'] / len(df) * 100:.1f}%",
            '平均客单价': f"¥{row['avg_amount']:,.2f}",
            '人均累计消费': f"¥{row['total_amount']:,.2f}",
            '平均消费频次': f"{row['transaction_count']:.1f}",
            '运营建议': actions[label],
        })
    quality = '结构清晰' if best_score >= .5 else '可用于初步运营分层' if best_score >= .25 else '群体边界较弱，建议补充更多行为特征'
    feature_labels = {
        'avg_amount': '客单价', 'total_amount': '累计净消费',
        'transaction_count': '消费频次', 'active_months': '活跃月份',
        'category_diversity': '品类广度',
    }
    selected_features = '、'.join(feature_labels[name] for name in model_features)
    insight = f"""
### 聚类结论
- 本次分析已自动保存为任务 **#{task_id}**，保存的 K 值和图中 K 值均为 **{best_k}**。
- 自动选择 **{best_k} 个客户群**，轮廓系数 **{best_score:.3f}**，当前聚类质量：**{quality}**。
- 模型实际使用 **{selected_features}**；近似常量指标会自动排除，但右侧仍保留全部业务指标便于完整对比。
- 左图中越靠右代表客单价越高，越靠上代表累计贡献越高，气泡越大代表活跃月份越多。
- 右侧热力图显示各群相对全体平均值的升降幅度，例如 `+20%` 表示高于平均水平 20%。
"""
    return fig, pd.DataFrame(summary_rows), insight


def user_segmentation_save():
    return '执行 KMeans 分析时已自动创建 task_id 并留存结果，无需重复保存。'


def churn_prediction():
    columns = ['客户', '流失概率', '风险等级', '预测结果']
    try:
        task_id = _run_v2_task('churn')
        rows = query("""
            SELECT c.customer_no || ' · ' || c.name AS customer,
                   p.churn_probability, p.predicted_label
            FROM ml.churn_prediction p
            JOIN biz.customer c ON c.customer_id = p.customer_id
            WHERE p.task_id = %s ORDER BY p.churn_probability DESC
        """, (task_id,))
        metrics = query("""
            SELECT metric_name, metric_value, dataset
            FROM ml.model_metric WHERE task_id = %s
        """, (task_id,))
    except Exception as exc:
        return (_empty_plot('流失模型运行失败'), _empty_plot('暂无风险分布'),
                pd.DataFrame(columns=columns), f'### 模型运行失败\n{exc}')

    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        return (_empty_plot('没有预测结果'), _empty_plot('暂无风险分布'),
                pd.DataFrame(columns=columns), '### 暂无结论\n没有可预测客户。')
    df['churn_probability'] = df['churn_probability'].astype(float)
    df['risk'] = pd.cut(df['churn_probability'], bins=[-0.01, .4, .7, 1.0],
                        labels=['低风险', '中风险', '高风险'])
    risk_colors = {'低风险': C_GREEN, '中风险': C_ORANGE, '高风险': C_RED}

    top = df.head(20).sort_values('churn_probability')
    fig_bar, ax_bar = plt.subplots(figsize=(12, 7))
    bars = ax_bar.barh(top['customer'], top['churn_probability'] * 100,
                       color=[risk_colors[str(risk)] for risk in top['risk']])
    for bar, value in zip(bars, top['churn_probability'] * 100):
        ax_bar.text(min(value + 1, 96), bar.get_y() + bar.get_height() / 2,
                    f'{value:.1f}%', va='center', color=FG, fontsize=8)
    ax_bar.axvline(40, color=C_ORANGE, linestyle='--', linewidth=1, label='中风险阈值 40%')
    ax_bar.axvline(70, color=C_RED, linestyle='--', linewidth=1, label='高风险阈值 70%')
    ax_bar.set_xlim(0, 100)
    ax_bar.set_xlabel('流失概率（%）')
    ax_bar.set_title('需要优先召回的高风险客户 Top 20')
    ax_bar.legend(facecolor='#252525', edgecolor='#444', labelcolor=FG, fontsize=8)
    dark_style(fig_bar, ax_bar)
    plt.tight_layout()

    counts = df['risk'].value_counts().reindex(['高风险', '中风险', '低风险'], fill_value=0)
    fig_dist, (ax_donut, ax_hist) = plt.subplots(1, 2, figsize=(12, 5.5))
    ax_donut.pie(counts.values, labels=[f'{name}\n{counts[name]:,} 人' for name in counts.index],
                 colors=[risk_colors[name] for name in counts.index], startangle=90,
                 wedgeprops={'width': .42, 'edgecolor': BG}, textprops={'color': FG, 'fontsize': 9})
    ax_donut.set_title('客户风险结构', color=FG)
    ax_donut.set_facecolor('#252525')
    ax_hist.hist(df['churn_probability'] * 100, bins=20, color=C_BLUE, alpha=.8, edgecolor=BG)
    ax_hist.axvline(40, color=C_ORANGE, linestyle='--', linewidth=1)
    ax_hist.axvline(70, color=C_RED, linestyle='--', linewidth=1)
    ax_hist.set_xlabel('流失概率（%）')
    ax_hist.set_ylabel('客户数量')
    ax_hist.set_title('概率分布：风险是否集中')
    dark_style(fig_dist, ax_hist)
    fig_dist.patch.set_facecolor(BG)
    plt.tight_layout()

    metric_map = {row['metric_name']: float(row['metric_value']) for row in metrics}
    auc = metric_map.get('auc')
    f1 = metric_map.get('f1')
    table = df.head(500).copy()
    table['流失概率'] = (table['churn_probability'] * 100).round(1).astype(str) + '%'
    table['预测结果'] = table['predicted_label'].map({True: '预计流失', False: '预计留存'})
    table = table[['customer', '流失概率', 'risk', '预测结果']]
    table.columns = columns
    high_count = int(counts['高风险'])
    auc_text = f'{auc:.3f}' if auc is not None else '未计算'
    f1_text = f'{f1:.3f}' if f1 is not None else '未计算'
    insight = f"""
### 模型结论
- 本次任务编号 **#{task_id}**，AUC **{auc_text}**，F1 **{f1_text}**。AUC 越接近 1，模型区分流失与留存客户的能力越强。
- 高风险客户 **{high_count:,}** 位；红色客户建议优先人工召回，橙色客户适合优惠券或内容触达。
- 虚线是风险阈值：超过 70% 为高风险，40% 至 70% 为中风险。结果已按任务自动保存，可回溯本次指标和预测。
"""
    return fig_bar, fig_dist, table, insight


def churn_save():
    return '流失预测在执行时已按 task_id 自动保存，无需重复写入。'


def amount_prediction_save():
    return '当前 V2 仅自动留存 RFM、KMeans 和流失分类任务；金额预测结果暂不写库。'


def sales_prediction_save():
    return '当前 V2 尚未定义销售额预测结果表，本次结果仅用于图形分析。'


def recommendation_save():
    return '当前 V2 尚未定义推荐结果表，本次结果仅用于图形分析。'


# ==================== Gradio UI ====================

def get_user_list():
    rows = query("SELECT username, full_name FROM users ORDER BY id")
    choices = [('全部用户', '全部用户')] + [
        (f"{r['username']} ({r['full_name']})", r['username']) for r in rows
    ]
    return choices


with gr.Blocks(theme=gr.themes.Soft(primary_hue='blue'), title='消费分析预测工作台 - 张跃星') as demo:
    gr.Markdown("""
    # 📈 消费分析预测工作台
    **作者：张跃星** | 数据来源: PostgreSQL consumer_analysis
    """)

    user_choices = get_user_list()
    user_default = '全部用户'

    with gr.Tabs():

        # ===== Tab 1: 数据概览 =====
        with gr.TabItem('📊 数据概览'):
            gr.Markdown("### 选择用户查看数据摘要")
            with gr.Row():
                sel_user_summary = gr.Dropdown(choices=user_choices, value=user_default, label='选择用户', scale=3)
                btn_summary = gr.Button('刷新概览', variant='primary', scale=1)
            summary_md = gr.Markdown("请选择用户后点击刷新")
            btn_summary.click(fn=data_summary, inputs=[sel_user_summary], outputs=[summary_md])
            demo.load(fn=data_summary, inputs=[sel_user_summary], outputs=[summary_md])

        # ===== Tab 2: 消费分析 =====
        with gr.TabItem('📈 消费分析'):
            gr.Markdown("### 多维度消费数据分析")
            with gr.Row():
                sel_user_analysis = gr.Dropdown(
                    choices=user_choices, value=user_default, label='选择用户', scale=2)
                sel_view = gr.Radio(
                    choices=['消费趋势（按月）', '消费分类统计', '商户消费排行', '地域消费分布'],
                    value='消费趋势（按月）', label='分析维度', scale=3)
                btn_analysis = gr.Button('生成图表', variant='primary', scale=1)
            analysis_plot_out = gr.Plot(label='分析结果')
            analysis_msg = gr.Textbox(label='信息', interactive=False)
            btn_analysis.click(fn=analysis_plot, inputs=[sel_view, sel_user_analysis],
                              outputs=[analysis_plot_out, analysis_msg])
            demo.load(fn=analysis_plot, inputs=[sel_view, sel_user_analysis],
                     outputs=[analysis_plot_out, analysis_msg])

        # ===== Tab 3: 趋势预测 =====
        with gr.TabItem('🔮 趋势预测'):
            gr.Markdown("### 消费趋势预测（Prophet / LSTM）")
            with gr.Row():
                sel_user_forecast = gr.Dropdown(
                    choices=user_choices, value=user_default, label='选择用户', scale=2)
                sel_model = gr.Radio(
                    choices=['Prophet', 'LSTM'], value='Prophet', label='预测模型', scale=2)
                sel_periods = gr.Slider(minimum=7, maximum=365, value=180, step=1, label='预测天数', scale=2)
                btn_forecast = gr.Button('开始预测', variant='primary', scale=1)
            forecast_plot_out = gr.Plot(label='预测结果')
            forecast_msg = gr.Textbox(label='预测摘要', interactive=False)

            with gr.Accordion("📋 LSTM 诊断日志 (展开查看详情)", open=False):
                forecast_log = gr.Textbox(label='', interactive=False, lines=18, max_lines=30,
                    elem_classes=['log-box'])

            btn_forecast.click(fn=forecast_run, inputs=[sel_user_forecast, sel_model, sel_periods],
                              outputs=[forecast_plot_out, forecast_msg, forecast_log])
            demo.load(fn=forecast_run, inputs=[sel_user_forecast, sel_model, sel_periods],
                     outputs=[forecast_plot_out, forecast_msg, forecast_log])

        # ===== Tab 4: 数据查询 =====
        with gr.TabItem('🔍 数据查询'):
            gr.Markdown("### 消费记录查询与导出")
            with gr.Row():
                sel_user_query = gr.Dropdown(choices=user_choices, value=user_default, label='用户', scale=2)
                txt_keyword = gr.Textbox(label='关键词搜索（商户/备注）', placeholder='输入关键词...', scale=3)
                n_rows = gr.Slider(minimum=10, maximum=500, value=50, step=10, label='返回行数', scale=1)
                btn_query = gr.Button('查询', variant='primary', scale=1)
            query_table = gr.Dataframe(label='查询结果', interactive=False)
            query_count = gr.Textbox(label='结果统计', interactive=False)

            def do_query(user_name, keyword, limit):
                if not user_name or user_name == '全部用户':
                    filt, params = '', ()
                else:
                    filt, params = 'AND u.username = %s', (user_name,)
                sql = f"""
                    SELECT s.spend_date, u.username, COALESCE(sc.category_name,'') as category,
                           s.amount, s.payment_method, COALESCE(m.merchant_name,'') as merchant,
                           COALESCE(cu.city,'') as city, COALESCE(s.remarks,'') as remarks
                    FROM spending_record s
                    JOIN users u ON s.user_id=u.id
                    LEFT JOIN spending_category sc ON s.category_id=sc.category_id
                    LEFT JOIN merchant m ON s.merchant_id=m.merchant_id
                    LEFT JOIN consumer_unit cu ON s.cu_id=cu.cu_id
                    WHERE 1=1 {filt} ORDER BY s.spend_date DESC LIMIT {int(limit)}
                """
                if keyword:
                    keyword = '%' + keyword.strip() + '%'
                    sql = sql.replace('ORDER BY', 'AND (m.merchant_name ILIKE %s OR s.remarks ILIKE %s) ORDER BY')
                    params = params + (keyword, keyword)
                rows_data = query(sql, params)
                if rows_data:
                    df_res = pd.DataFrame([dict(r) for r in rows_data])
                    return df_res, f'共 {len(df_res)} 条记录'
                return pd.DataFrame(), '无数据'

            btn_query.click(fn=do_query, inputs=[sel_user_query, txt_keyword, n_rows],
                           outputs=[query_table, query_count])
            demo.load(fn=do_query, inputs=[sel_user_query, txt_keyword, n_rows],
                     outputs=[query_table, query_count])

        # ===== Tab 5: RFM 分析 =====
        with gr.TabItem('🏷️ RFM 分析'):
            gr.Markdown("""
            ### RFM 客户价值分析
            **R（最近消费）**判断客户是否活跃，**F（消费频次）**判断黏性，**M（累计净消费）**判断价值。
            分析结果使用当前 V2 客户与支付/退款流水。
            """)
            with gr.Row():
                btn_rfm = gr.Button('🔄 执行RFM分析', variant='primary', scale=1)
                btn_rfm_save = gr.Button('查看保存说明', variant='secondary', scale=1)
            with gr.Row():
                rfm_scatter = gr.Plot(label='客户价值地图')
                rfm_bar = gr.Plot(label='客户分层结构')
            rfm_msg = gr.Markdown()
            rfm_table = gr.Dataframe(label='高优先级客户明细（最多500条）', interactive=False)
            rfm_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_rfm.click(fn=rfm_analysis, inputs=[], outputs=[rfm_scatter, rfm_bar, rfm_table, rfm_msg])
            btn_rfm_save.click(fn=rfm_save, inputs=[], outputs=[rfm_save_msg])

        # ===== Tab 6: 用户分群 =====
        with gr.TabItem('👥 用户分群'):
            gr.Markdown("""
            ### KMeans 客户聚类分群
            系统检测客单价、累计消费、消费频次、活跃月份和品类广度，自动排除近似常量指标，再把行为相近的客户归为同一群体。
            """)
            with gr.Row():
                btn_seg = gr.Button('🔄 执行聚类分析', variant='primary', scale=1)
                btn_seg_save = gr.Button('查看保存说明', variant='secondary', scale=1)
            seg_plot = gr.Plot(label='客户群位置与特征热力图')
            seg_msg = gr.Markdown()
            seg_table = gr.Dataframe(label='客户群业务画像与运营建议', interactive=False)
            seg_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_seg.click(fn=user_segmentation, inputs=[], outputs=[seg_plot, seg_table, seg_msg])
            btn_seg_save.click(fn=user_segmentation_save, inputs=[], outputs=[seg_save_msg])

        # ===== Tab 7: 流失预测 =====
        with gr.TabItem('⚠️ 流失预测'):
            gr.Markdown("""
            ### 客户流失风险分类
            模型使用历史观察窗生成流失/留存标签，输出每位客户的流失概率，并保留 AUC、F1 和任务结果。
            """)
            with gr.Row():
                btn_churn = gr.Button('🔄 执行流失预测', variant='primary', scale=1)
                btn_churn_save = gr.Button('查看保存说明', variant='secondary', scale=1)
            with gr.Row():
                churn_bar = gr.Plot(label='高风险客户 Top20')
                churn_pie = gr.Plot(label='风险结构与概率分布')
            churn_msg = gr.Markdown()
            churn_table = gr.Dataframe(label='流失风险明细（最多500条）', interactive=False)
            churn_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_churn.click(fn=churn_prediction, inputs=[], outputs=[churn_bar, churn_pie, churn_table, churn_msg])
            btn_churn_save.click(fn=churn_save, inputs=[], outputs=[churn_save_msg])

        # ===== Tab 8: 金额预测 =====
        with gr.TabItem('💰 金额预测'):
            gr.Markdown("### 线性回归预测未来3个月消费金额")
            with gr.Row():
                btn_amount = gr.Button('🔄 执行金额预测', variant='primary', scale=1)
                btn_amount_save = gr.Button('💾 保存到数据库', variant='secondary', scale=1)
            amount_plot = gr.Plot(label='预测趋势图')
            amount_table = gr.Dataframe(label='预测明细')
            amount_msg = gr.Textbox(label='分析信息', interactive=False)
            amount_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_amount.click(fn=amount_prediction, inputs=[], outputs=[amount_plot, amount_table, amount_msg])
            btn_amount_save.click(fn=amount_prediction_save, inputs=[], outputs=[amount_save_msg])

        # ===== Tab 9: 销售额预测 =====
        with gr.TabItem('📦 销售额预测'):
            gr.Markdown("### 未来3个月品类与商户销售额预测")
            with gr.Row():
                btn_sales = gr.Button('🔄 执行销售额预测', variant='primary', scale=1)
                btn_sales_save = gr.Button('💾 保存到数据库', variant='secondary', scale=1)
            sales_plot = gr.Plot(label='销售额预测图')
            sales_table = gr.Dataframe(label='预测明细')
            sales_msg = gr.Textbox(label='分析信息', interactive=False)
            sales_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_sales.click(fn=sales_prediction, inputs=[], outputs=[sales_plot, sales_table, sales_msg])
            btn_sales_save.click(fn=sales_prediction_save, inputs=[], outputs=[sales_save_msg])

        # ===== Tab 10: 推荐分析 =====
        with gr.TabItem('🎯 推荐分析'):
            gr.Markdown("### 协同过滤推荐分析")
            with gr.Row():
                btn_rec = gr.Button('🔄 执行推荐分析', variant='primary', scale=1)
                btn_rec_save = gr.Button('💾 保存到数据库', variant='secondary', scale=2)
            rec_plot = gr.Plot(label='推荐结果图')
            rec_table = gr.Dataframe(label='推荐明细')
            rec_msg = gr.Textbox(label='分析信息', interactive=False)
            rec_save_msg = gr.Textbox(label='保存结果', interactive=False)
            btn_rec.click(fn=recommendation_analysis, inputs=[], outputs=[rec_plot, rec_table, rec_msg])
            btn_rec_save.click(fn=recommendation_save, inputs=[], outputs=[rec_save_msg])


if __name__ == '__main__':
    port = int(os.environ.get('GRADIO_PORT', 7860))
    demo.launch(server_name='127.0.0.1', server_port=port, share=False, quiet=True)
    print(f'Gradio 分析预测工作台已启动: http://127.0.0.1:{port}')
