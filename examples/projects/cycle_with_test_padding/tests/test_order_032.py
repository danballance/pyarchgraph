"""Independent test consumer 032; never collected by the corpus harness."""
import orders

def test_order_surcharge_032():
    assert orders.ORDER_SURCHARGE == 2
