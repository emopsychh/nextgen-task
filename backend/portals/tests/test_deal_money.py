from decimal import Decimal

from portals.deal_money import apply_money_fields, as_money, deduct_spend_from_binding


def test_money_to_hours():
    result = apply_money_fields(
        hourly_rate_rub=as_money(2000),
        package_rub=as_money(40000),
        balance_rub=as_money(30000),
    )
    assert result["paid_hours"] == Decimal("20.00")
    assert result["remaining_hours"] == Decimal("15.00")
    assert result["package_rub"] == Decimal("40000.00")
    assert result["balance_rub"] == Decimal("30000.00")


def test_deduct_updates_balance():
    class Binding:
        remaining_hours = Decimal("15.00")
        hourly_rate_rub = Decimal("2000.00")
        balance_rub = Decimal("30000.00")

    binding = Binding()
    fields = deduct_spend_from_binding(binding, Decimal("1.50"))
    assert "remaining_hours" in fields
    assert "balance_rub" in fields
    assert binding.remaining_hours == Decimal("13.50")
    assert binding.balance_rub == Decimal("27000.00")
