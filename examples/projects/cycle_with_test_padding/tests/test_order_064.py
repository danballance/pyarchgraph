"""Independent test consumer 064; never collected by the corpus harness."""
import orders

def test_order_surcharge_064():
    assert orders.ORDER_SURCHARGE == 2
