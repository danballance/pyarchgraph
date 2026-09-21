"""Independent test consumer 009; never collected by the corpus harness."""
import orders

def test_order_surcharge_009():
    assert orders.ORDER_SURCHARGE == 2
