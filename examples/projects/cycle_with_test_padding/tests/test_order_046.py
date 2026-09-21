"""Independent test consumer 046; never collected by the corpus harness."""
import orders

def test_order_surcharge_046():
    assert orders.ORDER_SURCHARGE == 2
