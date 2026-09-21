"""Independent test consumer 011; never collected by the corpus harness."""
import orders

def test_order_surcharge_011():
    assert orders.ORDER_SURCHARGE == 2
