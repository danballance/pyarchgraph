"""Independent test consumer 006; never collected by the corpus harness."""
import orders

def test_order_surcharge_006():
    assert orders.ORDER_SURCHARGE == 2
