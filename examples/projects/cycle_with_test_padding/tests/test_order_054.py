"""Independent test consumer 054; never collected by the corpus harness."""
import orders

def test_order_surcharge_054():
    assert orders.ORDER_SURCHARGE == 2
