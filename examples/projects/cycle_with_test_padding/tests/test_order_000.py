"""Independent test consumer 000; never collected by the corpus harness."""
import orders

def test_order_surcharge_000():
    assert orders.ORDER_SURCHARGE == 2
