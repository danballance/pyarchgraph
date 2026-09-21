"""Independent test consumer 040; never collected by the corpus harness."""
import orders

def test_order_surcharge_040():
    assert orders.ORDER_SURCHARGE == 2
