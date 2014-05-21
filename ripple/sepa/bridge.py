import calendar
from datetime import timedelta, datetime
from decimal import Decimal
import json
import os
import binascii
from flask import Flask, request, Response, url_for, jsonify, render_template
from flask.ext.sqlalchemy import SQLAlchemy
import requests
from ripple_federation import Federation
from .utils import parse_sepa_data, add_response_headers, timesince


app = Flask(__name__)
app.config.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite:////tmp/sepalink.db')
app.config.setdefault('FIXED_FEE', Decimal('1.50'))
app.config.setdefault('VOLUME_FEE', Decimal('5'))
app.config.setdefault('BRIDGE_ADDRESS', 'rNrvihhhjDu6xmAzJBiKmEZDkjdYufh8s4')
app.config.setdefault('ACCEPTED_ISSUERS', ['rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B'])
app.config.setdefault('SEPA_URL', '')
app.config.setdefault('USE_HTTPS', False)
app.jinja_env.filters['timesince'] = timesince

db = SQLAlchemy(app)


@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        db.session.rollback()
    else:
        db.session.commit()


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
    # self.failed = 'unexpected'
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
        self.bic = self.iban = self.recipient_name = self.text = None


db.create_all()


CORS = {"Access-Control-Allow-Origin": "*"}


@app.route('/ripple.txt')
@add_response_headers(CORS)
def ripple_txt():
    """Format a ripple txt and expose some info about this service.
    """
    ripple_txt_options = {
        'domain': request.host,
        'federation_url': '{}://{}{}'.format(
            'https' if app.config['USE_HTTPS'] else 'http',
            request.host, url_for('federation')),
        'accounts': '\n'.join([app.config['BRIDGE_ADDRESS']])
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


@app.route('/federation')
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
            for issuer in app.config['ACCEPTED_ISSUERS']
        ],
        "quote_url": '{}://{}{}'.format(
            'https' if app.config['USE_HTTPS'] else 'http',
            request.host, url_for('quote')),
    }
    federation = Federation({request.host: config})

    # Validate the SEPA recipient
    try:
        parse_sepa_data(request.values['destination'])
    except ValueError as e:
        return jsonify(federation.error(
            'invalidSEPA', 'Cannot find a valid SEPA recipient: %s' % e))

    return jsonify(federation.endpoint(request.values, ))


@app.route('/quote')
@add_response_headers(CORS)
def quote():
    try:
        sepa = parse_sepa_data(request.values['destination'])
    except ValueError as e:
        return jsonify(Federation.error(
            'invalidSEPA', 'Cannot find a valid SEPA recipient: %s' % e))

    amount = request.values['amount'].split('/')

    if not amount[1] == 'EUR':
        raise ValueError()
    amount = Decimal(amount[0])

    # Determine the fee the user has to pay
    fee = Decimal(app.config.get('FIXED_FEE'))
    fee = fee + amount * (app.config.get('VOLUME_FEE')/100)

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
                    "issuer": app.config['ACCEPTED_ISSUERS'][0]
                }
            ],
            "address": app.config['BRIDGE_ADDRESS'],
            "expires": calendar.timegm(ticket.expires.timetuple())
        }
    })


@app.route('/on_payment')
def on_payment_received():
    """wasipaid.com will call this url when we receive a payment.
    """

    # Validate the notification
    result = requests.post('https://wasipaid.com/receipt', data=request.data)
    if result.text != 'VALID':
        return 'not at all ok', 400

    data = json.loads(request.data)
    payment = data['data']

    # Find the ticket
    ticket = Ticket.query.get(payment['destination'])
    if ticket:
        if Decimal(payment['amount']) == (ticket.amount + ticket.fee):
            # Call the SEPA backend
            result = requests.post(app.config['SEPA_API'], data={
                'name': ticket.recipient_name,
                'bic': ticket.bic,
                'iban': ticket.iban,
                'text': ticket.text,
                'verify': data['transaction']['TransactionHash']
            })
            result.raise_for_status()
            ticket.ripple_address = payment['account']
            ticket.state = 'queued'
            ticket.clear()
            return 'OK', 200

    # Can't handle the payment.
    ticket.failed = 'unexpected'

    return 'OK', 200


@app.route('/')
def index():
    tickets = Ticket.query.filter(
        Ticket.status!='quoted').order_by('-created_at')[:10]
    return render_template('index.html', tickets=tickets)
