"""Independent test consumer 071; never collected by the corpus harness."""
import orders

def test_order_surcharge_071():
    assert orders.ORDER_SURCHARGE == 2
