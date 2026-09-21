"""Independent test consumer 078; never collected by the corpus harness."""
import orders

def test_order_surcharge_078():
    assert orders.ORDER_SURCHARGE == 2
