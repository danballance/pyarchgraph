"""Independent test consumer 079; never collected by the corpus harness."""
import orders

def test_order_surcharge_079():
    assert orders.ORDER_SURCHARGE == 2
