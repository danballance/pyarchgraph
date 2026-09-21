"""Independent test consumer 028; never collected by the corpus harness."""
import orders

def test_order_surcharge_028():
    assert orders.ORDER_SURCHARGE == 2
