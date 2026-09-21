"""Independent test consumer 014; never collected by the corpus harness."""
import orders

def test_order_surcharge_014():
    assert orders.ORDER_SURCHARGE == 2
