"""Independent test consumer 029; never collected by the corpus harness."""
import orders

def test_order_surcharge_029():
    assert orders.ORDER_SURCHARGE == 2
