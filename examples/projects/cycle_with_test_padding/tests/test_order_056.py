"""Independent test consumer 056; never collected by the corpus harness."""
import orders

def test_order_surcharge_056():
    assert orders.ORDER_SURCHARGE == 2
