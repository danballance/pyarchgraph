"""Independent test consumer 069; never collected by the corpus harness."""
import orders

def test_order_surcharge_069():
    assert orders.ORDER_SURCHARGE == 2
