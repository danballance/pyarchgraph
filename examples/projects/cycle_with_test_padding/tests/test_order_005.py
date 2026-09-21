"""Independent test consumer 005; never collected by the corpus harness."""
import orders

def test_order_surcharge_005():
    assert orders.ORDER_SURCHARGE == 2
