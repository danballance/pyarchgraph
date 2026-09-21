"""Independent test consumer 070; never collected by the corpus harness."""
import orders

def test_order_surcharge_070():
    assert orders.ORDER_SURCHARGE == 2
