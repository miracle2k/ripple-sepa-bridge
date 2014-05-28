from decimal import Decimal
import confcollect
from flask import Flask
from flask.ext.sslify import SSLify
import logbook
from .model import db
from .bridge import bridge
from .utils import timesince


CONFIG_DEFAULTS = {
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///sepalink.db',
    'PGHOST': None,
    'PGUSER': None,
    'PGPASSWORD': None,
    'PGDATABASE': None,
    # Fixed fee to charge for every transaction.
    'FIXED_FEE': Decimal('1.50'),
    # Additional fee based on percentage of transfer amount
    'VOLUME_FEE': Decimal('5'),
    # Limits daily, and for individual transactions
    'TX_LIMIT': Decimal(100),
    'DAILY_TX_LIMIT': Decimal(500),
    # Ask client to pay to this address
    'BRIDGE_ADDRESS': None,
    # Ask client to pay EUR of one of these issuers
    'ACCEPTED_ISSUERS': [],
    # URL of the SEPA service to call
    'SEPA_API': None,
    # The postmark API config; the bridge will notify you if it receives
    # transactions that it cannot process.
    'POSTMARK_KEY': None,
    'POSTMARK_SENDER': None,
    # E-Mail addresses to send these notifications to.
    'ADMINS': [],
    # Disable to serve the bridge on unsecured HTTP. Useful in development
    # (with a modified client that uses HTTP).
    'USE_HTTPS': True,
    # URL for sentry error reporting
    'SENTRY_DSN': None
}


def create_app(config=None):
    """App-factory.
    """

    app = Flask(__name__)
    app.config.update(CONFIG_DEFAULTS)
    app.config.update(**config or {})
    app.config.update(confcollect.from_environ(by_defaults=app.config))

    # Validate config
    assert app.config.get('BRIDGE_ADDRESS')
    assert app.config.get('ACCEPTED_ISSUERS')
    assert app.config.get('POSTMARK_KEY')
    assert app.config.get('POSTMARK_SENDER')

    # Support specifying a postgres database url without anything.
    # I'd really like to find a good way of doing this outside.
    if app.config.get('PGHOST'):
        app.config['SQLALCHEMY_DATABASE_URI'] = \
            'postgres://{u}:{p}@{h}/{n}'.format(
                u=app.config['PGUSER'],
                p=app.config['PGPASSWORD'],
                h=app.config['PGHOST'],
                n=app.config['PGDATABASE'],
            )

    print('Using %s as database' % app.config['SQLALCHEMY_DATABASE_URI'])

    # In production, Flask doesn't even both to log errors to console,
    # which I judge to be a bit eccentric.
    logbook.StderrHandler(level='INFO').push_application()

    # Log to sentry on errors
    if app.config['SENTRY_DSN']:
        from raven.contrib.flask import Sentry
        sentry = Sentry(app, dsn=app.config['SENTRY_DSN'])

    # Add SSL support
    sslify = SSLify(app)

    # Setup app modules
    app.jinja_env.filters['timesince'] = timesince
    app.register_blueprint(bridge)

    # Make sure the database works.
    db.init_app(app)
    with app.app_context():
        db.create_all()

    return app
