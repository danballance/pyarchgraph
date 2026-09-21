"""Independent test consumer 088; never collected by the corpus harness."""
import orders

def test_order_surcharge_088():
    assert orders.ORDER_SURCHARGE == 2
