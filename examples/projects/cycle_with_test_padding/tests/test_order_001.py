"""Independent test consumer 001; never collected by the corpus harness."""
import orders

def test_order_surcharge_001():
    assert orders.ORDER_SURCHARGE == 2
