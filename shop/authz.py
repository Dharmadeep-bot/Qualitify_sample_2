"""What a signed-in caller may do: sign-in checks for routes and ownership of records."""

from __future__ import annotations

from functools import wraps

from flask import abort, current_app, g, request

from shop import auth


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            abort(401)
        claims = auth.read_session(header[len("Bearer "):], current_app.config["SETTINGS"])
        if claims is None:
            abort(401)
        g.user_id = int(claims["sub"])
        g.role = claims["role"]
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.role != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def own_order_or_404(order_id: int):
    """The order, only when it belongs to the caller (admins may see any).
    Someone else's order answers 404, the same as one that does not exist."""
    order = current_app.config["DB"].order(order_id)
    if order is None or (order["user_id"] != g.user_id and g.role != "admin"):
        abort(404)
    return order
