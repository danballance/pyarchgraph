"""Independent test consumer 052; never collected by the corpus harness."""
import orders

def test_order_surcharge_052():
    assert orders.ORDER_SURCHARGE == 2
