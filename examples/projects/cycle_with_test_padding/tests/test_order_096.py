"""Independent test consumer 096; never collected by the corpus harness."""
import orders

def test_order_surcharge_096():
    assert orders.ORDER_SURCHARGE == 2
