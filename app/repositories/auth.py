from sqlalchemy import text

from app.extensions import db


class AccountRepository:
    @staticmethod
    def list_accounts():
        return db.session.execute(text("""
            SELECT a.account_id, a.username, a.full_name, a.is_active,
                   a.last_login_at, a.created_at,
                   COALESCE(
                       array_agg(r.role_code ORDER BY ar.is_primary DESC, r.role_code)
                           FILTER (WHERE r.role_code IS NOT NULL),
                       ARRAY[]::varchar[]
                   ) AS roles
            FROM auth.account a
            LEFT JOIN auth.account_role ar ON ar.account_id = a.account_id
            LEFT JOIN auth.role r ON r.role_id = ar.role_id AND r.is_active
            GROUP BY a.account_id
            ORDER BY a.created_at, a.account_id
        """)).mappings().all()

    @staticmethod
    def is_super_admin(account_id):
        return bool(db.session.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM auth.account a
                JOIN auth.account_role ar ON ar.account_id = a.account_id
                JOIN auth.role r ON r.role_id = ar.role_id
                WHERE a.account_id = :account_id
                  AND a.is_active AND r.is_active
                  AND r.role_code = 'super_admin'
            )
        """), {"account_id": account_id}).scalar_one())

    @staticmethod
    def list_roles(assigner_id=None):
        return db.session.execute(text("""
            SELECT role_id, role_code, role_name, description
            FROM auth.role
            WHERE is_active
              AND (
                  role_code <> 'super_admin'
                  OR EXISTS (
                      SELECT 1
                      FROM auth.account a
                      JOIN auth.account_role ar ON ar.account_id = a.account_id
                      JOIN auth.role actor_role ON actor_role.role_id = ar.role_id
                      WHERE a.account_id = :assigner_id
                        AND a.is_active AND actor_role.is_active
                        AND actor_role.role_code = 'super_admin'
                  )
              )
            ORDER BY role_id
        """), {"assigner_id": assigner_id}).mappings().all()

    @staticmethod
    def permissions(account_id):
        with db.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT DISTINCT p.permission_code
                FROM auth.account a
                JOIN auth.account_role ar ON ar.account_id = a.account_id
                JOIN auth.role r ON r.role_id = ar.role_id AND r.is_active
                JOIN auth.role_permission rp ON rp.role_id = r.role_id
                JOIN auth.permission p ON p.permission_id = rp.permission_id AND p.is_active
                WHERE a.account_id = :account_id AND a.is_active
            """), {"account_id": account_id}).scalars().all()
        return frozenset(rows)

    @staticmethod
    def create(username, password_hash, full_name):
        return db.session.execute(text("""
            INSERT INTO auth.account (username, password_hash, full_name, role)
            VALUES (:username, :password_hash, :full_name, 'analyst')
            RETURNING account_id
        """), {
            "username": username,
            "password_hash": password_hash,
            "full_name": full_name,
        }).scalar_one()

    @staticmethod
    def set_roles(account_id, role_codes, assigned_by):
        requested_roles = set(role_codes)
        if "super_admin" in requested_roles and not AccountRepository.is_super_admin(assigned_by):
            raise ValueError("Only a super administrator can grant the super administrator role")
        roles = db.session.execute(text("""
            SELECT role_id, role_code
            FROM auth.role
            WHERE role_code = ANY(:role_codes) AND is_active
            ORDER BY role_id
        """), {"role_codes": list(role_codes)}).mappings().all()
        if len(roles) != len(requested_roles):
            raise ValueError("包含不存在或已停用的角色")
        db.session.execute(
            text("DELETE FROM auth.account_role WHERE account_id = :account_id"),
            {"account_id": account_id},
        )
        db.session.execute(text("""
            INSERT INTO auth.account_role (account_id, role_id, is_primary, assigned_by)
            VALUES (:account_id, :role_id, :is_primary, :assigned_by)
        """), [
            {
                "account_id": account_id,
                "role_id": role["role_id"],
                "is_primary": index == 0,
                "assigned_by": assigned_by,
            }
            for index, role in enumerate(roles)
        ])
        legacy_role = "analyst"
        if {role["role_code"] for role in roles} & {"super_admin", "system_admin"}:
            legacy_role = "admin"
        elif {role["role_code"] for role in roles} & {
            "customer_operator", "product_operator", "order_operator", "finance_auditor",
        }:
            legacy_role = "operator"
        db.session.execute(text("""
            UPDATE auth.account SET role = :legacy_role, updated_at = now()
            WHERE account_id = :account_id
        """), {"account_id": account_id, "legacy_role": legacy_role})

    @staticmethod
    def toggle(account_id, actor_id):
        return db.session.execute(text("""
            UPDATE auth.account
            SET is_active = NOT is_active, updated_at = now()
            WHERE account_id = :account_id
              AND (
                  EXISTS (
                      SELECT 1
                      FROM auth.account_role actor_assignment
                      JOIN auth.role actor_role ON actor_role.role_id = actor_assignment.role_id
                      WHERE actor_assignment.account_id = :actor_id
                        AND actor_role.role_code = 'super_admin' AND actor_role.is_active
                  )
                  OR NOT EXISTS (
                      SELECT 1
                      FROM auth.account_role target_assignment
                      JOIN auth.role target_role ON target_role.role_id = target_assignment.role_id
                      WHERE target_assignment.account_id = auth.account.account_id
                        AND target_role.role_code = 'super_admin' AND target_role.is_active
                  )
              )
            RETURNING is_active
        """), {"account_id": account_id, "actor_id": actor_id}).scalar_one_or_none()

    @staticmethod
    def update_password(account_id, password_hash, actor_id):
        return db.session.execute(text("""
            UPDATE auth.account
            SET password_hash = :password_hash, updated_at = now()
            WHERE account_id = :account_id
              AND (
                  EXISTS (
                      SELECT 1
                      FROM auth.account_role actor_assignment
                      JOIN auth.role actor_role ON actor_role.role_id = actor_assignment.role_id
                      WHERE actor_assignment.account_id = :actor_id
                        AND actor_role.role_code = 'super_admin' AND actor_role.is_active
                  )
                  OR NOT EXISTS (
                      SELECT 1
                      FROM auth.account_role target_assignment
                      JOIN auth.role target_role ON target_role.role_id = target_assignment.role_id
                      WHERE target_assignment.account_id = auth.account.account_id
                        AND target_role.role_code = 'super_admin' AND target_role.is_active
                  )
              )
        """), {
            "account_id": account_id,
            "password_hash": password_hash,
            "actor_id": actor_id,
        }).rowcount

    @staticmethod
    def login_logs(limit=200):
        return db.session.execute(text("""
            SELECT login_log_id, username, status, fail_reason, ip_address,
                   user_agent, created_at
            FROM audit.login_log
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

    @staticmethod
    def operation_logs(limit=200):
        return db.session.execute(text("""
            SELECT l.operation_log_id, a.username, l.action, l.entity_type,
                   l.entity_id, l.details, l.ip_address, l.created_at
            FROM audit.operation_log l
            LEFT JOIN auth.account a ON a.account_id = l.operator_id
            ORDER BY l.created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
