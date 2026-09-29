import pytest
from fiatium.domain import PaymentInput, fingerprint, processor_outcome
from pydantic import ValidationError

INVOICE = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("amount", [0, -1, 12.5, "100", True, 1_000_000_000_001])
def test_rejects_non_integer_or_out_of_bounds_money(amount):
    with pytest.raises(ValidationError):
        PaymentInput(invoice_id=INVOICE, amount=amount)


def test_fingerprint_includes_scenario_and_rejects_unsupported_currency():
    a = PaymentInput(invoice_id=INVOICE, amount=100)
    b = PaymentInput(invoice_id=INVOICE, amount=100, scenario="decline")
    assert fingerprint(a) != fingerprint(b)
    with pytest.raises(ValidationError):
        PaymentInput(invoice_id=INVOICE, amount=100, currency="USD")


def test_processor_scenarios_are_deterministic():
    assert processor_outcome("decline", 1) == "declined"
    assert processor_outcome("timeout", 1) == "timeout"
    assert processor_outcome("timeout", 2) == "settled"
    assert processor_outcome("delayed", 1) == "authorized"
    assert processor_outcome("delayed", 2) == "settled"
