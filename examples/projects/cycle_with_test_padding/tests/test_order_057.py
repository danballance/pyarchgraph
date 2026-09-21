"""Independent test consumer 057; never collected by the corpus harness."""
import orders

def test_order_surcharge_057():
    assert orders.ORDER_SURCHARGE == 2
