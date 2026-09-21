"""Independent test consumer 067; never collected by the corpus harness."""
import orders

def test_order_surcharge_067():
    assert orders.ORDER_SURCHARGE == 2
