"""Independent test consumer 094; never collected by the corpus harness."""
import orders

def test_order_surcharge_094():
    assert orders.ORDER_SURCHARGE == 2
