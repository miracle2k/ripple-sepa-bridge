import calendar
from datetime import timedelta, datetime
from decimal import Decimal
import os
import binascii
from flask import (
    Flask, request, Response, url_for, jsonify, render_template, Blueprint,
    current_app)
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.sslify import SSLify
import sqlalchemy
import logbook
from postmark import PMMail
import requests
import confcollect
from ripple_federation import Federation
from werkzeug.exceptions import BadRequest
from .utils import parse_sepa_data, add_response_headers, timesince


db = SQLAlchemy()

class Ticket(db.Model):
    """Tracks a transfer from initial quote to confirmed submission.

    Possible status values are:

    quoted - Temporary quote, will be deleted if no payment is made.
    received - We received the Ripple payment for the quote, and have
       queued up a bank transfer.
    sent - The SEPA backend has confirmed the execution of the transfer.
    confirmed - The SEPA backend has confirmed the transfer being executed
       on the bank end.
    """
    id = db.Column(db.String, primary_key=True)
    amount = db.Column(db.Numeric)
    fee = db.Column(db.Numeric)
    created_at = db.Column(db.DateTime(timezone=False))
    ripple_address = db.Column(db.String(255))
    status = db.Column(db.String(255), index=True)
    failed = db.Column(db.String(255), index=True)
    recipient_name = db.Column(db.String(255))
    bic = db.Column(db.String(255))
    iban = db.Column(db.String(255))
    text = db.Column(db.String(255))

    def __init__(self, amount=None, fee=None, name=None, bic=None,
                 iban=None, text=None):
        self.id = binascii.hexlify(os.urandom(256//8)).decode('ascii')
        self.amount = amount
        self.fee = fee
        self.recipient_name = name
        self.bic = bic
        self.iban = iban
        self.text = text
        self.created_at = datetime.utcnow()
        self.status = 'quoted'

    @property
    def expires(self):
        return self.created_at + timedelta(seconds=3600)

    @property
    def status_text(self):
        return {'quoted': 'Waiting for Ripple payment',
                'received': 'SEPA transfer in queue',
                'sent': 'SEPA transfer executed',
                'confirmed': 'SEPA transfer confirmed'}[self.status]

    def clear(self):
        self.bic = self.iban = self.recipient_name = self.text = ''

    @classmethod
    def tx_volume_today(cls):
        """Determine the volume handled by the bridge today.
        """
        today = datetime.utcnow().date()
        volume = (db.session
            .query(sqlalchemy.sql.func.sum(Ticket.amount))
            # Ignore quotes for which no payment was received
            .filter(Ticket.status!='quoted')
            # Only look at tickets from today
            .filter(sqlalchemy.func.date(Ticket.created_at) == today)
            # This does not work in SQLite, see:
            #   http://stackoverflow.com/questions/17333014/convert-selected-datetime-to-date-in-sqlalchemy#comment25152032_17334055
            #   http://sqlite.1065341.n5.nabble.com/CAST-td23755.html
            #.filter(cast(Ticket.created_at, sqlalchemy.types.Date)==today)
        ).one()[0]
        return volume or Decimal('0')


site = Blueprint('site', __name__, static_folder='static')


@site.teardown_app_request
def shutdown_session(exception=None):
    if exception:
        print("Requested ended with exception:", exception)
        # The gunicorn-bug exceptions are on the stack and can be printed.
        #import traceback
        #print(traceback.format_exc())
    if exception:
        db.session.rollback()
    else:
        db.session.commit()


CORS = {"Access-Control-Allow-Origin": "*"}


@site.route('/ripple.txt')
@add_response_headers(CORS)
def ripple_txt():
    """Format a ripple txt and expose some info about this service.
    """
    ripple_txt_options = {
        'domain': request.host,
        'federation_url': '{}://{}{}'.format(
            'https' if current_app.config['USE_HTTPS'] else 'http',
            request.host, url_for('.federation')),
        'accounts': '\n'.join([current_app.config['BRIDGE_ADDRESS']])
    }
    return Response("""
[domain]
{domain}

[federation_url]
{federation_url}

[accounts]
{accounts}
""".strip().format(**ripple_txt_options),
        mimetype='text/plain')


@site.route('/federation')
@add_response_headers(CORS)
def federation():
    """The federation endpoint. Answers quote requests from Ripple clients.
    """
    config = {
        "currencies": [
            {
                "currency": "EUR",
                "issuer": issuer
            }
            for issuer in current_app.config['ACCEPTED_ISSUERS']
        ],
        "quote_url": '{}://{}{}'.format(
            'https' if current_app.config['USE_HTTPS'] else 'http',
            request.host, url_for('.quote')),
    }
    federation = Federation({request.host: config})

    # Validate the SEPA recipient
    try:
        parse_sepa_data(request.values['destination'])
    except ValueError as e:
        return jsonify(federation.error(
            'invalidSEPA', 'Cannot find a valid SEPA recipient: %s' % e))

    return jsonify(federation.endpoint(request.values, ))


@site.route('/quote')
@add_response_headers(CORS)
def quote():
    try:
        sepa = parse_sepa_data(request.values['destination'])
    except ValueError as e:
        return jsonify(Federation.error(
            'invalidSEPA', 'Cannot find a valid SEPA recipient: %s' % e))

    amount = request.values['amount'].split('/')
    if len(amount) != 2:
        raise BadRequest()
    if not amount[1] == 'EUR':
        raise ValueError()
    amount = Decimal(amount[0])

    # Validate limits
    if current_app.config['TX_LIMIT']:
        if amount > Decimal(current_app.config['TX_LIMIT']):
            return jsonify(Federation.error(
                'limitExceeded',
                'The amount you are trying to send is too large (limit: %s)' %
                    current_app.config['TX_LIMIT']))
    if current_app.config['DAILY_TX_LIMIT']:
        cur_volume = Ticket.tx_volume_today()
        if amount + cur_volume > Decimal(current_app.config['DAILY_TX_LIMIT']):
            return jsonify(Federation.error(
                'limitExceeded',
                'We are currently unable to process such an amount, try '
                'again later.'))

    # Determine the fee the user has to pay
    fee = Decimal(current_app.config.get('FIXED_FEE'))
    fee = fee + amount * (Decimal(current_app.config.get('VOLUME_FEE'))/100)

    # Generate a quote id, store the thing in the database
    ticket = Ticket(amount=amount, fee=fee, **sepa)
    db.session.add(ticket)

    return jsonify({
        "result": "success",
        "quote": {
            "invoice_id": ticket.id,
            "send": [
                {
                    "currency": "EUR",
                    "value": "%s" % (ticket.amount + ticket.fee),
                    "issuer": current_app.config['ACCEPTED_ISSUERS'][0]
                }
            ],
            "address": current_app.config['BRIDGE_ADDRESS'],
            "expires": calendar.timegm(ticket.expires.timetuple())
        }
    })


@site.route('/on_payment', methods=['POST'])
def on_payment_received():
    """wasipaid.com will call this url when we receive a payment.
    """

    # Validate the notification
    # https://github.com/kennethreitz/requests/issues/2071
    result = requests.post(
        'https://wasipaid.com/receipt',
        data=request.get_data(), headers={
            'Content-Type': 'application/octet-stream'})
    if result.text != 'VALID':
        return 'not at all ok', 400

    payment = request.json['data']
    tx_hash = request.json['transaction']['hash']

    # Find the ticket
    ticket = Ticket.query.get(payment['invoice_id'].lower()) \
        if payment['invoice_id'] else None
    if ticket:
        if Decimal(payment['amount']) == (ticket.amount + ticket.fee):
            # Call the SEPA backend
            if current_app.config['SEPA_API']:
                result = requests.post(current_app.config['SEPA_API'], data={
                    'name': ticket.recipient_name,
                    'bic': ticket.bic,
                    'iban': ticket.iban,
                    'text': ticket.text,
                    'verify': tx_hash
                })
                result.raise_for_status()
            # If no backend is configured, send an email instead.
            else:
                send_mail(
                    'Payment received: Execute a transfer',
                    render_template('transfer.txt', **{'ticket': ticket}))

            ticket.ripple_address = payment['sender']
            ticket.status = 'received'
            ticket.clear()
            return 'OK', 200

        # Can't handle the payment.
        ticket.failed = 'unexpected'
        send_mail(
            'Received payment with unexpected amount',
            ('Transaction {tx} matches ticket {t}, but the '
                          'amounts do not match ({{txa}} vs {{ta}}).').format(
                   tx=tx_hash,
                   t=ticket.id,
                   txa=Decimal(payment['amount']),
                   ta=ticket.amount + ticket.fee
               )
        )
    else:
        send_mail(
            'Received unexpected payment',
            'Transaction {tx} does not match a ticket'.format(tx=tx_hash))


    return 'OK', 200


def send_mail(subject, text):
    PMMail(api_key=current_app.config['POSTMARK_KEY'],
           sender=current_app.config['POSTMARK_SENDER'],
           to=','.join(current_app.config['ADMINS']),
           subject=subject,
           text_body=text).send()


@site.route('/')
def index():
    tickets = Ticket.query.filter(
        Ticket.status!='quoted').order_by(Ticket.created_at.desc())[:10]
    return render_template('index.html', tickets=tickets)


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

    # Add SSL support
    sslify = SSLify(app)

    # Log to sentry on errors
    if app.config['SENTRY_DSN']:
        from raven.contrib.flask import Sentry
        sentry = Sentry(app, dsn=app.config['SENTRY_DSN'])

    # Setup app modules
    app.jinja_env.filters['timesince'] = timesince
    app.register_blueprint(site)

    # Make sure the database works.
    db.init_app(app)
    with app.app_context():
        db.create_all()

    return app
