from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from app.routes import logs_bp
from app.db import query
from pathlib import Path
import os


@logs_bp.route('/logs')
def logs_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    # Ensure logs directory exists
    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # Read from database system_logs if table exists
    table_exists = query(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'system_logs')"
    )
    db_logs = []
    if table_exists[0]['exists']:
        db_logs = query("""
            SELECT sl.*, u.username
            FROM system_logs sl
            LEFT JOIN users u ON sl.user_id = u.id
            ORDER BY sl.created_at DESC LIMIT 200
        """)

    log_path = log_dir / "system.log"
    file_content = ""
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            file_content = f.read()
    except Exception:
        pass

    # Also read login_logs if table exists
    login_table_exists = query(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'login_logs')"
    )
    login_logs = []
    if login_table_exists[0]['exists']:
        login_logs = query("""
            SELECT ll.login_time::TEXT, ll.logout_time::TEXT, ll.ip, ll.status, u.username
            FROM login_logs ll
            LEFT JOIN users u ON ll.user_id = u.id
            ORDER BY ll.login_time DESC LIMIT 100
        """)

    return render_template(
        'logs.html',
        db_logs=db_logs,
        file_content=file_content,
        login_logs=[dict(r) for r in login_logs] if login_logs else []
    )