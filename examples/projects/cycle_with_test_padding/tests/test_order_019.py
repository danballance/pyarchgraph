"""Independent test consumer 019; never collected by the corpus harness."""
import orders

def test_order_surcharge_019():
    assert orders.ORDER_SURCHARGE == 2
