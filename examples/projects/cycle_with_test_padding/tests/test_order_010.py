"""Independent test consumer 010; never collected by the corpus harness."""
import orders

def test_order_surcharge_010():
    assert orders.ORDER_SURCHARGE == 2
