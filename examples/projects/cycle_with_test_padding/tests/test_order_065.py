"""Independent test consumer 065; never collected by the corpus harness."""
import orders

def test_order_surcharge_065():
    assert orders.ORDER_SURCHARGE == 2
