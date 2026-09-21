"""Independent test consumer 050; never collected by the corpus harness."""
import orders

def test_order_surcharge_050():
    assert orders.ORDER_SURCHARGE == 2
