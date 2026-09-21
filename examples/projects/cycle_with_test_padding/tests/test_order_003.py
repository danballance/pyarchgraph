"""Independent test consumer 003; never collected by the corpus harness."""
import orders

def test_order_surcharge_003():
    assert orders.ORDER_SURCHARGE == 2
