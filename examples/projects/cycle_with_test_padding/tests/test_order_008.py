"""Independent test consumer 008; never collected by the corpus harness."""
import orders

def test_order_surcharge_008():
    assert orders.ORDER_SURCHARGE == 2
