"""Independent test consumer 020; never collected by the corpus harness."""
import orders

def test_order_surcharge_020():
    assert orders.ORDER_SURCHARGE == 2
