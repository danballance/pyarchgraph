"""Independent test consumer 058; never collected by the corpus harness."""
import orders

def test_order_surcharge_058():
    assert orders.ORDER_SURCHARGE == 2
