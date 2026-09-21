"""Independent test consumer 049; never collected by the corpus harness."""
import orders

def test_order_surcharge_049():
    assert orders.ORDER_SURCHARGE == 2
