"""Independent test consumer 087; never collected by the corpus harness."""
import orders

def test_order_surcharge_087():
    assert orders.ORDER_SURCHARGE == 2
