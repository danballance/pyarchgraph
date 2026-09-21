"""Independent test consumer 089; never collected by the corpus harness."""
import orders

def test_order_surcharge_089():
    assert orders.ORDER_SURCHARGE == 2
