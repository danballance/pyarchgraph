"""Independent test consumer 022; never collected by the corpus harness."""
import orders

def test_order_surcharge_022():
    assert orders.ORDER_SURCHARGE == 2
