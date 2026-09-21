"""Independent test consumer 002; never collected by the corpus harness."""
import orders

def test_order_surcharge_002():
    assert orders.ORDER_SURCHARGE == 2
