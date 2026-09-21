"""Independent test consumer 081; never collected by the corpus harness."""
import orders

def test_order_surcharge_081():
    assert orders.ORDER_SURCHARGE == 2
