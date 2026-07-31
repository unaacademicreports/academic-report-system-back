def register_blueprints(app):
    prefix = f"{app.config['API_PREFIX']}/{app.config['API_VERSION']}"

    from app.reports import reports_bp
    app.register_blueprint(reports_bp, url_prefix=prefix)

    from app.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix=prefix)

    from app.exports import exports_bp
    app.register_blueprint(exports_bp, url_prefix=prefix)
