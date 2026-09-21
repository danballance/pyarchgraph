"""Independent test consumer 017; never collected by the corpus harness."""
import orders

def test_order_surcharge_017():
    assert orders.ORDER_SURCHARGE == 2
