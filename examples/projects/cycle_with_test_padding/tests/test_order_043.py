"""Independent test consumer 043; never collected by the corpus harness."""
import orders

def test_order_surcharge_043():
    assert orders.ORDER_SURCHARGE == 2
