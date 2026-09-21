"""Independent test consumer 047; never collected by the corpus harness."""
import orders

def test_order_surcharge_047():
    assert orders.ORDER_SURCHARGE == 2
