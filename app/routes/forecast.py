from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from app.routes import forecast_bp
from app.db import query
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


@forecast_bp.route('/forecast')
def forecast_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('forecast.html')


@forecast_bp.route('/forecast/predict', methods=['POST'])
def predict():
    if 'user_id' not in session:
        return jsonify(success=False, message='请先登录')

    user_id = session['user_id']
    data = request.json or {}
    model_type = data.get('model', 'prophet')
    periods = min(int(data.get('periods', 30)), 365)

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return jsonify(success=False, message='pandas/numpy 未安装，请运行: pip install pandas numpy')

    # Get historical daily spending data
    rows = query("""
        SELECT spend_date, SUM(amount) as total
        FROM spending_record
        WHERE user_id = %s
        GROUP BY spend_date
        ORDER BY spend_date
    """, (user_id,))

    if not rows or len(rows) < 10:
        return jsonify(success=False, message='历史数据不足，至少需要10天记录')

    df = pd.DataFrame([(r['spend_date'], float(r['total'])) for r in rows],
                      columns=['ds', 'y'])
    df['ds'] = pd.to_datetime(df['ds'])

    if model_type == 'prophet':
        return _predict_prophet(df, periods)
    elif model_type == 'lstm':
        return _predict_lstm(df, periods)
    else:
        return jsonify(success=False, message='未知模型类型')


def _predict_prophet(df, periods):
    try:
        from prophet import Prophet
    except ImportError:
        return jsonify(success=False, message='Prophet 未安装，请运行: pip install prophet')

    m = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False)
    m.fit(df)
    future = m.make_future_dataframe(periods=periods)
    forecast = m.predict(future)

    return jsonify(success=True, model='prophet',
        historical_dates=[d.strftime('%Y-%m-%d') for d in df['ds']],
        historical_values=[float(v) for v in df['y']],
        forecast_dates=[d.strftime('%Y-%m-%d') for d in forecast['ds']],
        forecast_values=[max(0, round(float(v), 2)) for v in forecast['yhat']],
        forecast_lower=[max(0, round(float(v), 2)) for v in forecast['yhat_lower']],
        forecast_upper=[max(0, round(float(v), 2)) for v in forecast['yhat_upper']])


def _predict_lstm(df, periods):
    try:
        import torch
        import torch.nn as nn
        import numpy as np
    except ImportError:
        return jsonify(success=False, message='PyTorch 未安装，请运行: pip install torch')

    class LSTMModel(nn.Module):
        def __init__(self, input_size=1, hidden_size=32, num_layers=1, output_size=1):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.1)
            self.fc = nn.Linear(hidden_size, output_size)

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    data_vals = df['y'].values.astype(np.float64)

    data_min, data_max = float(data_vals.min()), float(data_vals.max())
    if data_max - data_min < 0.01:
        return jsonify(success=False, message='数据变化太小，无法预测')

    data_norm = (data_vals - data_min) / (data_max - data_min)

    seq_len = min(10, max(3, len(data_norm) // 3))
    X, Y = [], []
    for i in range(len(data_norm) - seq_len):
        X.append(data_norm[i:i + seq_len])
        Y.append(data_norm[i + seq_len])

    if len(X) < 5:
        return jsonify(success=False, message='数据量不足')

    X_tensor = torch.tensor(np.array(X)).unsqueeze(-1).float().to(device)
    Y_tensor = torch.tensor(np.array(Y)).float().to(device)

    model = LSTMModel(input_size=1, hidden_size=min(32, len(data_norm)//2), output_size=1).to(device)
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

    # Generate future predictions
    model.eval()
    last_seq = data_norm[-seq_len:].copy()
    future_preds = []
    pred_periods = min(periods, 60)
    for _ in range(pred_periods):
        with torch.no_grad():
            inp = torch.tensor(last_seq).unsqueeze(0).unsqueeze(-1).float().to(device)
            pred = model(inp).item()
            future_preds.append(max(0, float(pred * (data_max - data_min) + data_min)))
            last_seq = np.roll(last_seq, -1)
            last_seq[-1] = pred

    last_date = df['ds'].max().to_pydatetime() if hasattr(df['ds'].max(), 'to_pydatetime') else df['ds'].max()
    future_dates = [last_date + timedelta(days=i + 1) for i in range(len(future_preds))]

    return jsonify(success=True, model='lstm',
        historical_dates=[d.strftime('%Y-%m-%d') for d in df['ds']],
        historical_values=[float(v) for v in df['y']],
        forecast_dates=[d.strftime('%Y-%m-%d') for d in future_dates],
        forecast_values=[float(round(v, 2)) for v in future_preds])
