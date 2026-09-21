"""Independent test consumer 059; never collected by the corpus harness."""
import orders

def test_order_surcharge_059():
    assert orders.ORDER_SURCHARGE == 2
