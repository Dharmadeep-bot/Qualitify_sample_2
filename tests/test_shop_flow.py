"""The shop end to end through its HTTP routes, plus the new modules on their own."""

import json
import secrets
import time
from types import SimpleNamespace

import pytest

from shop import audit, auth, cart, catalog, coupons, notifications, webhooks
from shop.app import create_app
from shop.config import Settings
from shop.db import CouponRejected, Database, OutOfStock


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOP_SIGNING_KEY", secrets.token_hex(32))
    monkeypatch.setenv("SHOP_PAYMENT_URL", "https://payments.example.com/charge")
    monkeypatch.setenv("SHOP_PAYMENT_CREDENTIAL", "test-credential")
    monkeypatch.setenv("SHOP_MAIL_URL", "https://mail.example.com/send")
    monkeypatch.setenv("SHOP_MAIL_KEY", "test-mail-key")
    monkeypatch.setenv("SHOP_WEBHOOK_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("SHOP_DB", str(tmp_path / "shop.db"))
    settings = Settings()
    app = create_app(settings)
    db: Database = app.config["DB"]
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, email, password_hash, role) VALUES (1, 'a@x.io', ?, 'customer')", (auth.hash_password("pw-one-long-enough"),))
        conn.execute("INSERT INTO users (id, email, password_hash, role) VALUES (2, 'admin@x.io', ?, 'admin')", (auth.hash_password("pw-two-long-enough"),))
        conn.execute("INSERT INTO products (id, sku, name, price_cents) VALUES (1, 'MOUSE-1', 'Wireless mouse', 2500)")
        conn.execute("INSERT INTO products (id, sku, name, price_cents) VALUES (2, 'KEYB-1', 'Keyboard', 9000)")
        conn.execute("INSERT INTO stock (product_id, quantity) VALUES (1, 5)")
        conn.execute("INSERT INTO stock (product_id, quantity) VALUES (2, 1)")
    return SimpleNamespace(app=app, client=app.test_client(), db=db, settings=settings)


def _auth(env, user_id, role="customer"):
    return {"Authorization": "Bearer " + auth.issue_session(user_id, role, env.settings)}


# ---- catalogue ----------------------------------------------------------------


def test_products_are_paged_with_a_cursor(env):
    first = env.client.get("/products?size=1").get_json()
    assert len(first["products"]) == 1 and first["next"] == 1
    second = env.client.get(f"/products?size=1&after={first['next']}").get_json()
    assert second["products"][0]["sku"] == "KEYB-1" and second["next"] is None


def test_search_treats_wildcards_as_text_and_refuses_empty_terms(env):
    assert [p["sku"] for p in env.client.get("/products/search?q=mouse").get_json()["products"]] == ["MOUSE-1"]
    assert env.client.get("/products/search?q=%25").get_json()["products"] == []
    assert env.client.get("/products/search?q=").status_code == 400
    assert env.client.get("/products/search?q=" + "x" * 200).status_code == 400


def test_page_size_is_capped():
    assert catalog.clamp_page_size(10_000) == catalog.MAX_PAGE_SIZE and catalog.clamp_page_size(0) == 1


# ---- checkout -------------------------------------------------------------------


def test_checkout_prices_from_the_catalogue_not_the_request(env):
    body = {"items": [{"sku": "MOUSE-1", "quantity": 2, "price_cents": 1}]}
    reply = env.client.post("/checkout", json=body, headers=_auth(env, 1))
    assert reply.status_code == 201 and reply.get_json()["total_cents"] == 5000


def test_checkout_needs_a_session(env):
    assert env.client.post("/checkout", json={"items": []}).status_code == 401


@pytest.mark.parametrize(
    "items",
    [[], "x", [{"sku": "nope", "quantity": 1}], [{"sku": "MOUSE-1", "quantity": 0}], [{"sku": "MOUSE-1", "quantity": True}],
     [{"sku": "MOUSE-1", "quantity": 21}], [{"sku": "UNKNOWN-9", "quantity": 1}]],
)
def test_a_bad_cart_is_a_400(env, items):
    assert env.client.post("/checkout", json={"items": items}, headers=_auth(env, 1)).status_code == 400


def test_running_out_of_stock_changes_nothing_and_keeps_the_coupon(env):
    code = coupons.create(env.db, 10, 30, 5)
    body = {"items": [{"sku": "KEYB-1", "quantity": 1}, {"sku": "MOUSE-1", "quantity": 6}], "coupon": code}
    assert env.client.post("/checkout", json=body, headers=_auth(env, 1)).status_code == 409
    with env.db.connect() as conn:
        assert conn.execute("SELECT quantity FROM stock WHERE product_id = 2").fetchone()[0] == 1
        assert conn.execute("SELECT used FROM coupons WHERE code = ?", (code,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_a_coupon_discounts_once_and_runs_out(env):
    code = coupons.create(env.db, 20, 30, 1)
    body = {"items": [{"sku": "MOUSE-1", "quantity": 1}], "coupon": code}
    ok = env.client.post("/checkout", json=body, headers=_auth(env, 1))
    assert ok.get_json()["total_cents"] == 2000
    assert env.client.post("/checkout", json=body, headers=_auth(env, 1)).status_code == 422


def test_the_last_unit_goes_to_one_buyer_only(env):
    body = {"items": [{"sku": "KEYB-1", "quantity": 1}]}
    assert env.client.post("/checkout", json=body, headers=_auth(env, 1)).status_code == 201
    assert env.client.post("/checkout", json=body, headers=_auth(env, 2, "admin")).status_code == 409


def test_creating_an_order_directly_raises_the_right_errors(env):
    with pytest.raises(OutOfStock):
        env.db.create_order_with_items(1, [(1, 99, 2500)], None, int(time.time()), cart.apply_percent)
    with pytest.raises(CouponRejected):
        env.db.create_order_with_items(1, [(1, 1, 2500)], "NOSUCHCODE12", int(time.time()), cart.apply_percent)


# ---- cart rules -----------------------------------------------------------------


def test_repeated_skus_are_added_up_and_still_capped():
    assert cart.parse_cart([{"sku": "AB-1", "quantity": 3}, {"sku": "AB-1", "quantity": 4}]) == {"AB-1": 7}
    with pytest.raises(ValueError):
        cart.parse_cart([{"sku": "AB-1", "quantity": 15}, {"sku": "AB-1", "quantity": 15}])


def test_percent_off_never_goes_below_one_cent():
    assert cart.apply_percent(1, 50) == 1 and cart.apply_percent(1000, 25) == 750
    with pytest.raises(ValueError):
        cart.apply_percent(1000, 51)


# ---- coupons --------------------------------------------------------------------


def test_coupon_codes_are_long_unique_and_unambiguous():
    codes = {coupons.new_code() for _ in range(200)}
    assert len(codes) == 200 and all(len(c) == 12 and not set(c) & set("01OI") for c in codes)
    assert coupons.normalise(" abcd-efgh ") is None and coupons.normalise("abcdefghjk23") == "ABCDEFGHJK23"


# ---- webhooks -------------------------------------------------------------------


def _signed(secret, payload, when=None):
    body = json.dumps(payload).encode()
    stamp = int(when if when is not None else time.time())
    return body, {"X-Timestamp": str(stamp), "X-Signature": webhooks.sign(secret, stamp, body)}


def test_a_signed_payment_event_marks_the_order_paid_once(env):
    order_id = env.client.post("/checkout", json={"items": [{"sku": "MOUSE-1", "quantity": 1}]}, headers=_auth(env, 1)).get_json()["order_id"]
    body, headers = _signed(env.settings.webhook_secret, {"id": "evt_1", "type": "payment.succeeded", "data": {"order_id": order_id, "reference": "ref-9"}})
    assert env.client.post("/webhooks/payments", data=body, headers=headers).get_json()["result"] == "applied"
    assert env.client.post("/webhooks/payments", data=body, headers=headers).get_json()["result"] == "duplicate"
    assert env.db.order(order_id)["status"] == "paid"


def test_unsigned_stale_or_wrongly_signed_events_are_refused(env):
    body, headers = _signed(env.settings.webhook_secret, {"id": "e", "type": "x"})
    assert env.client.post("/webhooks/payments", data=body).status_code == 401
    assert env.client.post("/webhooks/payments", data=body, headers={**headers, "X-Signature": "0" * 64}).status_code == 401
    old_body, old_headers = _signed(env.settings.webhook_secret, {"id": "e", "type": "x"}, when=time.time() - 3600)
    assert env.client.post("/webhooks/payments", data=old_body, headers=old_headers).status_code == 401
    other_body, other_headers = _signed("another-secret-another-secret-12345", {"id": "e", "type": "x"})
    assert env.client.post("/webhooks/payments", data=other_body, headers=other_headers).status_code == 401


# ---- notifications --------------------------------------------------------------


class _Reply:
    def __init__(self, status):
        self.status_code = status


def test_mail_retries_temporary_failures_but_not_permanent_ones(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json)
        return _Reply(503 if len(calls) < 3 else 202)

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send("https://m", "k", "a@x.io", "Hi\r\nBcc: evil@x.io", "text", sleep=lambda _s: None)
    assert len(calls) == 3 and "\n" not in calls[0]["subject"]

    calls.clear()
    monkeypatch.setattr(notifications.requests, "post", lambda *a, **kw: (calls.append(1), _Reply(400))[1])
    assert not notifications.send("https://m", "k", "a@x.io", "s", "t", sleep=lambda _s: None) and len(calls) == 1


# ---- audit ----------------------------------------------------------------------


def test_audit_entries_hide_secrets(env):
    audit.record(env.db, 1, "login", payment_token="tok_123", note="ok")
    with env.db.connect() as conn:
        detail = conn.execute("SELECT detail FROM audit_log").fetchone()[0]
    assert "tok_123" not in detail and "[hidden]" in detail and "ok" in detail


# ---- admin ----------------------------------------------------------------------


def test_admin_routes_are_closed_to_customers(env):
    assert env.client.post("/admin/coupons", json={}, headers=_auth(env, 1)).status_code == 403
    assert env.client.post("/admin/stock", json={}, headers=_auth(env, 1)).status_code == 403
    made = env.client.post("/admin/coupons", json={"percent": 10, "valid_days": 5, "max_uses": 3}, headers=_auth(env, 2, "admin"))
    assert made.status_code == 201 and len(made.get_json()["code"]) == 12


def test_restocking_adds_to_what_is_there(env):
    reply = env.client.post("/admin/stock", json={"sku": "KEYB-1", "quantity": 4}, headers=_auth(env, 2, "admin"))
    assert reply.status_code == 200
    with env.db.connect() as conn:
        assert conn.execute("SELECT quantity FROM stock WHERE product_id = 2").fetchone()[0] == 5
