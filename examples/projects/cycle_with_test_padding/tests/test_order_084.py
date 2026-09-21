"""Independent test consumer 084; never collected by the corpus harness."""
import orders

def test_order_surcharge_084():
    assert orders.ORDER_SURCHARGE == 2
