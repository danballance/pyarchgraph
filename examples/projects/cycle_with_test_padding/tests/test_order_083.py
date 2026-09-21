"""Independent test consumer 083; never collected by the corpus harness."""
import orders

def test_order_surcharge_083():
    assert orders.ORDER_SURCHARGE == 2
