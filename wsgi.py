#!/usr/bin/env python3

import confcollect
from ripple.sepa import create_app

try:
    config = confcollect.from_module('config', silent=False)
except ImportError:
    print('No local config file exists, relying on environment')
    config = {}

app = create_app(config=config)


if __name__ == '__main__':
    import sys
    app.debug = True
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
