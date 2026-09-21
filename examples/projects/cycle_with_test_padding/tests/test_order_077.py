"""Independent test consumer 077; never collected by the corpus harness."""
import orders

def test_order_surcharge_077():
    assert orders.ORDER_SURCHARGE == 2
