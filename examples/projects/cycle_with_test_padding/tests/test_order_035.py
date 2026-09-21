"""Independent test consumer 035; never collected by the corpus harness."""
import orders

def test_order_surcharge_035():
    assert orders.ORDER_SURCHARGE == 2
