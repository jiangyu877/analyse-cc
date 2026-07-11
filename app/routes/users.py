from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from app.routes import users_bp
from app.db import query, execute
from app.utils import hash_password


@users_bp.route('/users')
def users_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    if session.get('role') != 'admin':
        return redirect(url_for('main.dashboard'))
    users = query("""
        SELECT u.id, u.username, u.full_name, u.email, r.name as role,
               CASE WHEN u.status = 1 THEN true ELSE false END as is_active,
               u.created_at::TEXT, u.last_login_at::TEXT
        FROM users u LEFT JOIN roles r ON u.role_id = r.id
        ORDER BY u.id
    """)
    return render_template('users.html', users=users, current_user=session.get('username'))


@users_bp.route('/users/add', methods=['POST'])
def add_user():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify(success=False, message='无权限')
    data = request.json
    try:
        role_id = query("SELECT id FROM roles WHERE name = %s", (data['role'],))
        if not role_id:
            return jsonify(success=False, message='角色不存在')
        password_hash = hash_password(data['password'])
        execute("""
            INSERT INTO users(username, password_hash, full_name, email, role_id, status)
            VALUES (%s, %s, %s, %s, %s, 1)
        """, (data['username'], password_hash, data.get('full_name', ''),
              data.get('email', ''), role_id[0]['id']))
        return jsonify(success=True, message='用户添加成功')
    except Exception as e:
        if 'unique' in str(e).lower():
            return jsonify(success=False, message='用户名已存在')
        return jsonify(success=False, message=str(e))


@users_bp.route('/users/edit', methods=['POST'])
def edit_user():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify(success=False, message='无权限')
    data = request.json
    try:
        role_id = query("SELECT id FROM roles WHERE name = %s", (data['role'],))
        if not role_id:
            return jsonify(success=False, message='角色不存在')
        execute("UPDATE users SET full_name=%s, email=%s, role_id=%s WHERE id=%s",
                (data.get('full_name', ''), data.get('email', ''),
                 role_id[0]['id'], int(data['user_id'])))
        return jsonify(success=True, message='用户信息已更新')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@users_bp.route('/users/delete', methods=['POST'])
def delete_user():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify(success=False, message='无权限')
    user_id = request.json.get('user_id')
    if int(user_id) == session['user_id']:
        return jsonify(success=False, message='不能删除自己')
    try:
        execute("DELETE FROM users WHERE id = %s", (user_id,))
        return jsonify(success=True, message='用户已删除')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@users_bp.route('/users/password', methods=['POST'])
def change_password():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify(success=False, message='无权限')
    data = request.json
    try:
        password_hash = hash_password(data['password'])
        execute("UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hash, int(data['user_id'])))
        return jsonify(success=True, message='密码已更新')
    except Exception as e:
        return jsonify(success=False, message=str(e))


@users_bp.route('/users/roles')
def get_roles():
    roles = query("SELECT name FROM roles ORDER BY id")
    return jsonify([r['name'] for r in roles])
