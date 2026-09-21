"""Independent test consumer 097; never collected by the corpus harness."""
import orders

def test_order_surcharge_097():
    assert orders.ORDER_SURCHARGE == 2
