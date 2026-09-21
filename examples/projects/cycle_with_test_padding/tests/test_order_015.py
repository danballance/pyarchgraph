"""Independent test consumer 015; never collected by the corpus harness."""
import orders

def test_order_surcharge_015():
    assert orders.ORDER_SURCHARGE == 2
