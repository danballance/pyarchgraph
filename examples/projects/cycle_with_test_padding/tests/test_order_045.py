"""Independent test consumer 045; never collected by the corpus harness."""
import orders

def test_order_surcharge_045():
    assert orders.ORDER_SURCHARGE == 2
