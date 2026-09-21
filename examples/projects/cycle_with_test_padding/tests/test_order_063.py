"""Independent test consumer 063; never collected by the corpus harness."""
import orders

def test_order_surcharge_063():
    assert orders.ORDER_SURCHARGE == 2
