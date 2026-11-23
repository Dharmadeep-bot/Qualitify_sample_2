from shop.refund_history import RefundLine, history_for_user


class _FakeDb:
    def orders_of(self, user_id):
        return [{"id": 1, "total_cents": 5000}, {"id": 2, "total_cents": 900}]

    def refunds_of(self, order_id):
        return [{"amount_cents": 1000}, {"amount_cents": 500}] if order_id == 1 else []


def test_history_adds_up_the_refunds_of_each_order():
    lines = history_for_user(_FakeDb(), 7)
    assert lines == [RefundLine(1, 5000, 1500), RefundLine(2, 900, 0)]
    assert lines[0].left_cents == 3500
