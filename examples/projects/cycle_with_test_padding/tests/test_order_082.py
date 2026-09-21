"""Independent test consumer 082; never collected by the corpus harness."""
import orders

def test_order_surcharge_082():
    assert orders.ORDER_SURCHARGE == 2
