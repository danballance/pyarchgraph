"""Independent test consumer 060; never collected by the corpus harness."""
import orders

def test_order_surcharge_060():
    assert orders.ORDER_SURCHARGE == 2
