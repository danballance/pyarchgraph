"""Independent test consumer 004; never collected by the corpus harness."""
import orders

def test_order_surcharge_004():
    assert orders.ORDER_SURCHARGE == 2
