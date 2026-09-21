"""Independent test consumer 026; never collected by the corpus harness."""
import orders

def test_order_surcharge_026():
    assert orders.ORDER_SURCHARGE == 2
