"""Independent test consumer 051; never collected by the corpus harness."""
import orders

def test_order_surcharge_051():
    assert orders.ORDER_SURCHARGE == 2
