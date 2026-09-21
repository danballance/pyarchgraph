"""Independent test consumer 072; never collected by the corpus harness."""
import orders

def test_order_surcharge_072():
    assert orders.ORDER_SURCHARGE == 2
