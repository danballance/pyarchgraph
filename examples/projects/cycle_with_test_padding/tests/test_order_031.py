"""Independent test consumer 031; never collected by the corpus harness."""
import orders

def test_order_surcharge_031():
    assert orders.ORDER_SURCHARGE == 2
