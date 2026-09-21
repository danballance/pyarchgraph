"""Independent test consumer 027; never collected by the corpus harness."""
import orders

def test_order_surcharge_027():
    assert orders.ORDER_SURCHARGE == 2
