"""Independent test consumer 090; never collected by the corpus harness."""
import orders

def test_order_surcharge_090():
    assert orders.ORDER_SURCHARGE == 2
