"""Independent test consumer 038; never collected by the corpus harness."""
import orders

def test_order_surcharge_038():
    assert orders.ORDER_SURCHARGE == 2
