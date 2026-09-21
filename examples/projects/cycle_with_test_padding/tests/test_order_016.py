"""Independent test consumer 016; never collected by the corpus harness."""
import orders

def test_order_surcharge_016():
    assert orders.ORDER_SURCHARGE == 2
