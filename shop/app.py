"""HTTP API of the shop. Every route that touches a user's data requires a
valid session, takes the user from that session (never from the request), and
checks that the record asked for belongs to them."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

from flask import Flask, abort, g, jsonify, render_template, request

from shop import auth, authz, payments, reports, uploads
from shop.config import Settings
from shop.db import Database

log = logging.getLogger(__name__)

_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS = 8
_attempts: dict[str, deque] = defaultdict(deque)

_ERRORS = {
    400: "bad request",
    401: "sign-in required",
    403: "not allowed",
    404: "not found",
    409: "this order cannot be changed now",
    413: "request too large",
    429: "too many attempts, try again later",
}


def _too_many_attempts(who: str) -> bool:
    now = time.monotonic()
    recent = _attempts[who]
    while recent and now - recent[0] > _LOGIN_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        return True
    recent.append(now)
    return False


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _text(data: dict, key: str, max_length: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        abort(400)
    return value


def create_app(settings: Optional[Settings] = None) -> Flask:
    settings = settings or Settings()
    app = Flask(__name__)
    app.config.update(SETTINGS=settings, DB=Database(settings.database), MAX_CONTENT_LENGTH=3_000_000)
    db: Database = app.config["DB"]
    db.init()

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    for code, message in _ERRORS.items():
        app.register_error_handler(code, lambda error, message=message: (jsonify(error=message), error.code))

    @app.errorhandler(500)
    def server_error(_error):
        return jsonify(error="something went wrong"), 500

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/login")
    def login():
        data = _json_body()
        email = _text(data, "email", 254)
        plain = _text(data, "password", 72)
        if _too_many_attempts(request.remote_addr or "unknown"):
            abort(429)
        session = auth.login(email, plain, db, settings)
        if session is None:
            return jsonify(error="invalid credentials"), 401
        return jsonify(session=session)

    @app.get("/orders")
    @authz.login_required
    def my_orders():
        return jsonify(orders=[dict(row) for row in db.orders_of(g.user_id)])

    @app.get("/orders/<int:order_id>")
    @authz.login_required
    def one_order(order_id: int):
        return jsonify(order=dict(authz.own_order_or_404(order_id)))

    @app.get("/orders/<int:order_id>/quote")
    @authz.login_required
    def quote(order_id: int):
        order = authz.own_order_or_404(order_id)
        try:
            cents = reports.apply_discount(request.args.get("discount", "0"), order["total_cents"])
        except ValueError:
            abort(400)
        return jsonify(total_cents=cents)

    @app.post("/orders/<int:order_id>/pay")
    @authz.login_required
    def pay(order_id: int):
        order = authz.own_order_or_404(order_id)
        if order["status"] != "new":
            abort(409)
        payment_token = _text(_json_body(), "payment_token", 200)
        reference = payments.charge(order["id"], order["total_cents"], payment_token, settings)
        if reference is None:
            return jsonify(error="the payment did not go through"), 502
        db.mark_paid(order["id"], reference)
        return jsonify(status="paid")

    @app.post("/orders/<int:order_id>/refund")
    @authz.admin_required
    def refund(order_id: int):
        order = db.order(order_id)
        if order is None:
            abort(404)
        try:
            requested = payments.to_cents(_json_body().get("amount"))
            cents = payments.validate_refund(order["total_cents"], db.refunded_cents(order["id"]), requested)
        except ValueError:
            abort(400)
        db.add_refund(order["id"], cents)
        log.info("refund of %s cents on order %s by user %s", cents, order["id"], g.user_id)
        return jsonify(refunded_cents=cents), 201

    @app.get("/admin/export")
    @authz.admin_required
    def export():
        user_ids = request.args.getlist("user", type=int)[:50]
        if not user_ids:
            abort(400)
        rows = reports.order_summary(user_ids, db)
        written = reports.export_csv(rows, settings.export_dir)
        return jsonify(file=written.name, rows=len(rows))

    @app.post("/uploads")
    @authz.login_required
    def upload():
        incoming = request.files.get("file")
        if incoming is None:
            abort(400)
        try:
            stored = uploads.save_upload(incoming, settings.upload_dir)
        except ValueError:
            abort(400)
        return jsonify(stored=stored), 201

    @app.get("/hello")
    def hello():
        return render_template("hello.html", name=request.args.get("name", "friend")[:50])

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
