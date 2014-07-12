#!/bin/sh

# This is how you might test a payment callback to the bridge (set RECEIPT_DEBUGGING=True).

http POST http://127.0.0.1:8080/on_payment data:='{"invoice_id": "655ed6593ff490f06577e90c8837198858c3b3e557be20744ddc9d9b77abf3fb", "amount": "43.50", "sender": "rXXX"}' transaction:='{"hash": "sdf"}
