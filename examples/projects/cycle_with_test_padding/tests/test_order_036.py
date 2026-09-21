"""Independent test consumer 036; never collected by the corpus harness."""
import orders

def test_order_surcharge_036():
    assert orders.ORDER_SURCHARGE == 2
