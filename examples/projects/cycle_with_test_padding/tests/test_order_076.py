"""Independent test consumer 076; never collected by the corpus harness."""
import orders

def test_order_surcharge_076():
    assert orders.ORDER_SURCHARGE == 2
