"""Independent test consumer 075; never collected by the corpus harness."""
import orders

def test_order_surcharge_075():
    assert orders.ORDER_SURCHARGE == 2
