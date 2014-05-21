Ripple SEPA Outbound Bridge
===========================

This is the Open Source version of the SEPA Outbound Bridge for Ripple that
runs on [sepa.link](http://sepa.link).

What you find here is a web app written in Python which implements the
outbound bridge protocol. It will give out quotes, keep track of them,
and can issue outbound bank payment requests.

To actually process a payments end-to-end, it relies on two external services:

1. [wasipaid.com](http://wasipaid.com) will tell the bridge about incoming
   Ripple payments.

2. To make outbound SEPA payments, it sends a POST request to an external
   HTTP API. It is up to you to provide an implementation here. You could
   use a service like Currency Cloud, or interact with your own bank.
