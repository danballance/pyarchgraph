"""Independent test consumer 092; never collected by the corpus harness."""
import orders

def test_order_surcharge_092():
    assert orders.ORDER_SURCHARGE == 2
