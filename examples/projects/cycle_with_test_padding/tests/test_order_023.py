"""Independent test consumer 023; never collected by the corpus harness."""
import orders

def test_order_surcharge_023():
    assert orders.ORDER_SURCHARGE == 2
