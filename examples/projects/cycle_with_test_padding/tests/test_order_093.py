"""Independent test consumer 093; never collected by the corpus harness."""
import orders

def test_order_surcharge_093():
    assert orders.ORDER_SURCHARGE == 2
