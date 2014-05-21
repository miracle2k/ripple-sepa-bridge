#!/usr/bin/env python3

from ripple.sepa import app

if __name__ == '__main__':
    import sys
    app.debug = True
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
