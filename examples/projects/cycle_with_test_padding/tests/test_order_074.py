"""Independent test consumer 074; never collected by the corpus harness."""
import orders

def test_order_surcharge_074():
    assert orders.ORDER_SURCHARGE == 2
