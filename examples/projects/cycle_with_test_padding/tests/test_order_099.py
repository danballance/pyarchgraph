"""Independent test consumer 099; never collected by the corpus harness."""
import orders

def test_order_surcharge_099():
    assert orders.ORDER_SURCHARGE == 2
