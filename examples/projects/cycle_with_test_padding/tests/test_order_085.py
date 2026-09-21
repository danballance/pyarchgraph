"""Independent test consumer 085; never collected by the corpus harness."""
import orders

def test_order_surcharge_085():
    assert orders.ORDER_SURCHARGE == 2
