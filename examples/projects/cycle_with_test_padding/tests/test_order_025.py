"""Independent test consumer 025; never collected by the corpus harness."""
import orders

def test_order_surcharge_025():
    assert orders.ORDER_SURCHARGE == 2
