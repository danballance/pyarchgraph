"""Independent test consumer 095; never collected by the corpus harness."""
import orders

def test_order_surcharge_095():
    assert orders.ORDER_SURCHARGE == 2
