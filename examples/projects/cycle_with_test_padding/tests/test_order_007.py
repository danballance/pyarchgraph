"""Independent test consumer 007; never collected by the corpus harness."""
import orders

def test_order_surcharge_007():
    assert orders.ORDER_SURCHARGE == 2
