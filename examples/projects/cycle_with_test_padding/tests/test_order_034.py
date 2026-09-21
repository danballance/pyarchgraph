"""Independent test consumer 034; never collected by the corpus harness."""
import orders

def test_order_surcharge_034():
    assert orders.ORDER_SURCHARGE == 2
