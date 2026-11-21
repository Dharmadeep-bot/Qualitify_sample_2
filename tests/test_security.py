import secrets
import time
from types import SimpleNamespace

import jwt
import pytest

from shop import auth, payments, reports


def _settings(seconds: int = 60) -> SimpleNamespace:
    return SimpleNamespace(signing_key=secrets.token_hex(32), session_seconds=seconds)


def test_a_session_round_trips_and_carries_the_role():
    settings = _settings()
    claims = auth.read_session(auth.issue_session(7, "customer", settings), settings)
    assert claims["sub"] == "7" and claims["role"] == "customer"


def test_an_expired_or_tampered_or_foreign_session_is_refused():
    settings = _settings(seconds=-5)
    assert auth.read_session(auth.issue_session(1, "customer", settings), settings) is None  # expired

    good = _settings()
    raw = auth.issue_session(1, "customer", good)
    assert auth.read_session(raw[:-3] + "abc", good) is None  # tampered
    assert auth.read_session(raw, _settings()) is None  # signed with another key
    forged = jwt.encode({"sub": "1", "role": "admin", "exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256")
    assert auth.read_session(forged, good) is None


def test_passwords_are_hashed_and_compared_properly():
    stored = auth.hash_password("a long enough phrase")
    assert stored != b"a long enough phrase"
    assert auth.check_password("a long enough phrase", stored)
    assert not auth.check_password("another phrase", stored)


def test_reset_codes_are_eight_digits_and_not_repeated():
    codes = {auth.new_reset_code() for _ in range(50)}
    assert len(codes) > 40 and all(len(c) == 8 and c.isdigit() for c in codes)


@pytest.mark.parametrize("bad", [0, -5, "abc", True, None, 10**9])
def test_refund_amounts_must_be_sensible_numbers(bad):
    with pytest.raises(ValueError):
        payments.to_cents(bad)


def test_a_refund_can_never_exceed_what_is_left():
    assert payments.to_cents("12.50") == 1250
    assert payments.validate_refund(5000, 1000, 4000) == 4000
    with pytest.raises(ValueError):
        payments.validate_refund(5000, 1000, 4001)


def test_discounts_are_parsed_never_evaluated():
    assert reports.apply_discount("10", 10000) == 9000
    for bad in ("__import__('os').system('x')", "-1", "90", "abc"):
        with pytest.raises(ValueError):
            reports.apply_discount(bad, 10000)


def test_exports_neutralise_spreadsheet_formulas_and_stay_in_their_folder(tmp_path):
    path = reports.export_csv([{"user_id": "=1+1", "orders": 2, "total_cents": 300}], str(tmp_path))
    assert path.parent == tmp_path.resolve()
    assert "'=1+1" in path.read_text(encoding="utf-8")
