"""Independent test consumer 048; never collected by the corpus harness."""
import orders

def test_order_surcharge_048():
    assert orders.ORDER_SURCHARGE == 2
