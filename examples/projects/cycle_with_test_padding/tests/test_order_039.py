"""Independent test consumer 039; never collected by the corpus harness."""
import orders

def test_order_surcharge_039():
    assert orders.ORDER_SURCHARGE == 2
