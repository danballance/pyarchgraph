"""Independent test consumer 086; never collected by the corpus harness."""
import orders

def test_order_surcharge_086():
    assert orders.ORDER_SURCHARGE == 2
