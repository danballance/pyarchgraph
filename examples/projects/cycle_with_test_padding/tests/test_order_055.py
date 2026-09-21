"""Independent test consumer 055; never collected by the corpus harness."""
import orders

def test_order_surcharge_055():
    assert orders.ORDER_SURCHARGE == 2
