"""Independent test consumer 033; never collected by the corpus harness."""
import orders

def test_order_surcharge_033():
    assert orders.ORDER_SURCHARGE == 2
