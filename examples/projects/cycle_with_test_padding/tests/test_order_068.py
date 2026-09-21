"""Independent test consumer 068; never collected by the corpus harness."""
import orders

def test_order_surcharge_068():
    assert orders.ORDER_SURCHARGE == 2
