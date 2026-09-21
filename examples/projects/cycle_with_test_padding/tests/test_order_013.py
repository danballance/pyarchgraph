"""Independent test consumer 013; never collected by the corpus harness."""
import orders

def test_order_surcharge_013():
    assert orders.ORDER_SURCHARGE == 2
