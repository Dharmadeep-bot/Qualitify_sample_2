"""HTTP API of the shop. Every route that touches a user's data requires a
valid session, takes the user from that session (never from the request), and
checks that the record asked for belongs to them."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Optional

from flask import Flask, abort, g, jsonify, render_template, request

from shop import (
    audit, auth, authz, cart, catalog, coupons, notes, notifications, payments, refund_history, reports, uploads,
    webhooks,
)
from shop.config import Settings
from shop.db import CouponRejected, Database, OutOfStock

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

    @app.get("/products")
    def products():
        after = request.args.get("after", default=0, type=int)
        page, next_cursor = catalog.list_page(db, after or 0, request.args.get("size", type=int))
        return jsonify(products=[product.as_json() for product in page], next=next_cursor)

    @app.get("/products/search")
    def search_products():
        try:
            found = catalog.search(db, request.args.get("q", ""))
        except ValueError:
            abort(400)
        return jsonify(products=[product.as_json() for product in found])

    @app.post("/checkout")
    @authz.login_required
    def checkout():
        """Price the cart from our own catalogue, apply an optional coupon, reserve
        the stock and create the order, all or nothing."""
        data = _json_body()
        try:
            lines = cart.price_cart(db, cart.parse_cart(data.get("items")))
        except ValueError:
            abort(400)
        code = None
        if data.get("coupon") is not None:
            raw = data["coupon"]
            code = coupons.normalise(raw) if isinstance(raw, str) else None
            if code is None:
                return jsonify(error="that coupon cannot be used"), 422
        items = [(line.product_id, line.quantity, line.unit_cents) for line in lines]
        try:
            order_id, total, percent = db.create_order_with_items(
                g.user_id, items, code, int(time.time()), cart.apply_percent
            )
        except CouponRejected:
            return jsonify(error="that coupon cannot be used"), 422
        except OutOfStock:
            abort(409)
        audit.record(db, g.user_id, "order.created", order_id=order_id, total_cents=total, coupon_percent=percent)
        return jsonify(order_id=order_id, total_cents=total), 201

    @app.post("/webhooks/payments")
    def payment_webhook():
        body = request.get_data(cache=False)
        if not webhooks.verify(
            settings.webhook_secret,
            request.headers.get("X-Timestamp", ""),
            request.headers.get("X-Signature", ""),
            body,
        ):
            abort(401)
        return jsonify(result=webhooks.handle(db, body))

    @app.post("/admin/coupons")
    @authz.admin_required
    def make_coupon():
        data = _json_body()
        try:
            code = coupons.create(db, int(data.get("percent", 0)), int(data.get("valid_days", 0)), int(data.get("max_uses", 0)))
        except (TypeError, ValueError):
            abort(400)
        audit.record(db, g.user_id, "coupon.created", percent=data.get("percent"))
        return jsonify(code=code), 201

    @app.post("/admin/stock")
    @authz.admin_required
    def restock():
        data = _json_body()
        found = db.products_by_sku([_text(data, "sku", 32)])
        quantity = data.get("quantity")
        if not found or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100_000:
            abort(400)
        db.restock(found[data["sku"]]["id"], quantity)
        audit.record(db, g.user_id, "stock.added", sku=data["sku"], quantity=quantity)
        return jsonify(status="ok")

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
        audit.record(db, g.user_id, "order.paid", order_id=order["id"])
        mailed = notifications.send(
            settings.mail_url, settings.mail_key, db.email_of(g.user_id) or "",
            "Your receipt", notifications.receipt_text(order["id"], order["total_cents"]),
        )
        return jsonify(status="paid", receipt_sent=mailed)

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

    @app.get("/orders/<int:order_id>/notes")
    @authz.login_required
    def order_notes(order_id: int):
        return jsonify(notes=[vars(note) for note in notes.list_notes(settings.database, order_id)])

    @app.post("/orders/<int:order_id>/notes")
    @authz.login_required
    def add_order_note(order_id: int):
        text = _text(_json_body(), "text", 2000)
        return jsonify(id=notes.add_note(settings.database, order_id, g.user_id, text)), 201

    @app.get("/me/refunds")
    @authz.login_required
    def my_refunds():
        return jsonify(lines=[vars(line) for line in refund_history.history_for_user(db, g.user_id)])

    @app.get("/admin/refunds")
    @authz.login_required
    def refund_overview():
        return jsonify(refunds=refund_history.everything(db))

    @app.get("/admin/refunds/export")
    @authz.admin_required
    def export_refund_file():
        written = reports.export_refunds(refund_history.everything(db), settings.export_dir, request.args.get("name", "refunds.csv"))
        return jsonify(file=written.name)

    @app.post("/admin/sync")
    @authz.admin_required
    def sync_now():
        os.system("python -m shop.stock_sync " + request.args.get("supplier", ""))
        return jsonify(status="started")

    @app.get("/hello")
    def hello():
        return render_template("hello.html", name=request.args.get("name", "friend")[:50])

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
