from decimal import Decimal
import confcollect
from flask import Flask
from flask.ext.sslify import SSLify
import logbook
from .model import db
from .admin import admin
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
    'USER_TX_LIMIT': Decimal(100),
    'BRIDGE_TX_LIMIT': Decimal(500),
    # Ask client to pay to this address
    'BRIDGE_ADDRESS': None,
    # Ask client to pay EUR of one of these issuers. Due to
    # https://ripplelabs.atlassian.net/browse/WC-1855 only the first
    # issuer will actually be considered by the client.
    #
    # If you leave this empty the bridge responds in such a way that
    # any currency accepted by the bridge account is considered.
    'ACCEPTED_ISSUERS': [],
    # URL of the SEPA service to call
    'SEPA_API': None,
    'SEPA_API_AUTH': None,
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
    'SENTRY_DSN': None,
    # Passwords for the admin interface. If none are given, it will
    # be disabled.
    'ADMIN_AUTH': {}
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

    # Enable the admin
    if app.config['ADMIN_AUTH']:
        admin.init_app(app)

    # Make sure the database works.
    db.init_app(app)
    with app.app_context():
        db.create_all()

    return app
