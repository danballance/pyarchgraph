"""Independent test consumer 066; never collected by the corpus harness."""
import orders

def test_order_surcharge_066():
    assert orders.ORDER_SURCHARGE == 2
