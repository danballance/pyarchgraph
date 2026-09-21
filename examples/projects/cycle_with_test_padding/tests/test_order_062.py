"""Independent test consumer 062; never collected by the corpus harness."""
import orders

def test_order_surcharge_062():
    assert orders.ORDER_SURCHARGE == 2
