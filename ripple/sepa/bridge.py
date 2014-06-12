import calendar
from decimal import Decimal

from flask import (
    request, Response, url_for, jsonify, render_template, Blueprint,
    current_app)
from postmark import PMMail
import requests
from werkzeug.exceptions import BadRequest

from ripple.sepa.model import db, Ticket
from ripple_federation import Federation
from .utils import parse_sepa_data, add_response_headers


bridge = Blueprint('bridge', __name__, static_folder='static')


@bridge.teardown_app_request
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


@bridge.route('/ripple.txt')
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


@bridge.route('/federation')
@add_response_headers(CORS)
def federation():
    """The federation endpoint. This basically just points the client
    to the url of the quoting service.

    Note that the SEPA recipient is NOT validated here; the Ripple client
    will only show the user error messages that occur during the quote.
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

    return jsonify(federation.endpoint(request.values, ))


@bridge.route('/quote')
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
        return jsonify(Federation.error(
            'invalidAmount', 'You can only send EUR.'))
    amount = Decimal(amount[0])

    # Make sure the amount isn't dividing up any cents.
    if amount.quantize(Decimal('0.00')) != amount:
        return jsonify(Federation.error(
                'invalidAmount', 'The amount must be divisible by 1 cent'))

    # Validate limits
    if current_app.config['USER_TX_LIMIT']:
        cur_volume = Ticket.tx_volume_today(sepa['iban'])
        if amount + cur_volume > Decimal(current_app.config['USER_TX_LIMIT']):
            return jsonify(Federation.error(
                'limitExceeded',
                'The amount you are trying to send is too large (limit: %s)' %
                    current_app.config['USER_TX_LIMIT']))
    if current_app.config['BRIDGE_TX_LIMIT']:
        cur_volume = Ticket.tx_volume_today()
        if amount + cur_volume > Decimal(current_app.config['BRIDGE_TX_LIMIT']):
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
                    "issuer": issuer
                } for issuer in current_app.config['ACCEPTED_ISSUERS']
            ],
            "address": current_app.config['BRIDGE_ADDRESS'],
            "expires": calendar.timegm(ticket.expires.timetuple())
        }
    })


@bridge.route('/on_payment', methods=['POST'])
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


@bridge.route('/')
def index():
    tickets = Ticket.query.filter(
        Ticket.status!='quoted').order_by(Ticket.created_at.desc())[:10]
    return render_template(
        'index.html', tickets=tickets, config=current_app.config,
        Decimal=Decimal)


