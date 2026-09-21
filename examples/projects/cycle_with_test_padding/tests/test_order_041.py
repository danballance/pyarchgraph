"""Independent test consumer 041; never collected by the corpus harness."""
import orders

def test_order_surcharge_041():
    assert orders.ORDER_SURCHARGE == 2
