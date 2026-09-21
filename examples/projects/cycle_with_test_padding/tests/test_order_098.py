"""Independent test consumer 098; never collected by the corpus harness."""
import orders

def test_order_surcharge_098():
    assert orders.ORDER_SURCHARGE == 2
