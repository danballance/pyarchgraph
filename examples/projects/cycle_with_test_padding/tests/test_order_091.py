"""Independent test consumer 091; never collected by the corpus harness."""
import orders

def test_order_surcharge_091():
    assert orders.ORDER_SURCHARGE == 2
