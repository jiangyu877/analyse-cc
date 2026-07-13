from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    (config_object or Config).validate()

    db.init_app(app)
    csrf.init_app(app)
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.custom_query import custom_query_bp
    from app.routes.customers import customers_bp
    from app.routes.products import products_bp
    from app.routes.orders import orders_bp
    from app.routes.payments import payments_bp
    from app.routes.refunds import refunds_bp
    from app.routes.algorithms import algorithms_bp
    from app.routes.system import system_bp
    from app.routes.imports import imports_bp
    from app.routes.admin import admin_bp
    from app.routes.knowledge import knowledge_bp
    from app.routes.qa import qa_bp
    from app.routes.reports import reports_bp

    for blueprint in (
        auth_bp,
        main_bp,
        customers_bp,
        products_bp,
        orders_bp,
        payments_bp,
        refunds_bp,
        algorithms_bp,
        custom_query_bp,
        system_bp,
        imports_bp,
        admin_bp,
        knowledge_bp,
        qa_bp,
        reports_bp,
    ):
        app.register_blueprint(blueprint)

    @app.after_request
    def secure_response(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net data:; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'self'",
        )
        return response

    @app.context_processor
    def authorization_context():
        from flask import session

        from app.repositories.auth import AccountRepository

        permissions = frozenset()
        if session.get("user_id") is not None:
            permissions = AccountRepository.permissions(session["user_id"])
        return {"can": lambda permission: permission in permissions}

    @app.errorhandler(400)
    def bad_request(error):
        if request.is_json:
            return jsonify(success=False, message=str(error)), 400
        return render_template("error.html", code=400, message="请求参数不正确"), 400

    @app.errorhandler(403)
    def forbidden(error):
        if request.is_json:
            return jsonify(success=False, message="无权执行此操作"), 403
        return render_template("error.html", code=403, message="无权执行此操作"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="页面不存在"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("error.html", code=500, message="系统处理失败，请查看服务日志"), 500

    return app
