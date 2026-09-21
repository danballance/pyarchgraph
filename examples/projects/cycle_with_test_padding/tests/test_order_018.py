"""Independent test consumer 018; never collected by the corpus harness."""
import orders

def test_order_surcharge_018():
    assert orders.ORDER_SURCHARGE == 2
