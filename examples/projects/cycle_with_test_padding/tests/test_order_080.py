"""Independent test consumer 080; never collected by the corpus harness."""
import orders

def test_order_surcharge_080():
    assert orders.ORDER_SURCHARGE == 2
