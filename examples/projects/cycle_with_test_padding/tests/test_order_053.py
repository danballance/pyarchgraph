"""Independent test consumer 053; never collected by the corpus harness."""
import orders

def test_order_surcharge_053():
    assert orders.ORDER_SURCHARGE == 2
