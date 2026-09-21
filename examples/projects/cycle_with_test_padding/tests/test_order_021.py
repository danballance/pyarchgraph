"""Independent test consumer 021; never collected by the corpus harness."""
import orders

def test_order_surcharge_021():
    assert orders.ORDER_SURCHARGE == 2
