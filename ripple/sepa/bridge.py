import calendar
from decimal import Decimal
import json

from flask import (
    request, Response, url_for, jsonify, render_template, Blueprint,
    current_app)
from postmark import PMMail
import requests
from requests.exceptions import RequestException
from werkzeug.exceptions import BadRequest

from ripple.sepa.model import db, Ticket
from ripple_federation import Federation
from .utils import add_response_headers, parse_sepa_destination, validate_sepa


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
    def handle_request(domain, user):
        # The user can specify SEPA data in the destination already.
        # This allows linking to a pre-filled SEPA payment form.
        try:
            defaults = parse_sepa_destination(user)
        except ValueError as e:
            defaults = {'name': '', 'iban': '', 'bic': '', 'text': ''}

        config = {
            "extra_fields": [
                {
                    "label": "Name of Recipient",
                    "hint": "Required.",
                    "required": True,
                    "name": "name",
                    "value": defaults['name'],
                    "type": "text"
                },
                {
                    "label": "IBAN",
                    "hint": "Required. Will look something like this: GB82WEST12345698765432",
                    "required": True,
                    "name": "iban",
                    "value": defaults['iban'],
                    "type": "text"
                },
                {
                    "label": "BIC",
                    "name": "bic",
                    "hint": "Required. Will look something like this: DABADKKK",
                    "required": True,
                    "value": defaults['bic'],
                    "type": "text"
                },
                {
                    "label": "Text",
                    "hint": "Optional",
                    "name": "text",
                    "required": False,
                    "value": defaults['text'],
                    "type": "text"
                }
            ],
            "currencies":
                # Either list all specific issuers we accept, or just say EUR.
                [{"currency": "EUR"}]
                if not current_app.config['ACCEPTED_ISSUERS']
                else
                [
                    {
                        "currency": "EUR",
                        "issuer": issuer
                    }
                    for issuer in (current_app.config['ACCEPTED_ISSUERS'])]
            ,
            "quote_url": '{}://{}{}'.format(
                'https' if current_app.config['USE_HTTPS'] else 'http',
                request.host, url_for('.quote')),
        }


        return config

    federation = Federation({request.host: handle_request})
    return jsonify(federation.endpoint(request.values, ))


@bridge.route('/quote')
@add_response_headers(CORS)
def quote():
    sepa = {
        'bic': request.values.get('bic', ''),
        'iban': request.values.get('iban', ''),
        'name': request.values.get('name', ''),
        'text': request.values.get('text', ''),
    }
    try:
        validate_sepa(sepa)
    except ValueError as e:
        return jsonify(Federation.error(
            'invalidSEPA', '%s' % e))

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
            # Accept either an explicit list of issuers, or - by specifying
            # the bridge destination address as the issuer, accept any issue
            # the bridge as trustlines for.
            # https://ripplelabs.atlassian.net/browse/WC-1855
            "send": [
                {
                    "currency": "EUR",
                    "value": "%s" % (ticket.amount + ticket.fee),
                    "issuer": issuer
                } for issuer in (current_app.config['ACCEPTED_ISSUERS'] or
                        [current_app.config['BRIDGE_ADDRESS']])
            ],
            "address": current_app.config['BRIDGE_ADDRESS'],
            "expires": calendar.timegm(ticket.expires.timetuple())
        }
    })


@bridge.route('/on_payment', methods=['POST'])
def on_payment_received():
    """wasipaid.com will call this url when we receive a payment.

    TODO: To make 100% sure we do not send duplicate payment requests
    to the backend, this view manually puts tickets into a "sending"
    state before contacting the backend; a conflict can then be resolved
    manually. However, I would prefer for the backend to be responsible
    for this.
    """

    # Validate the notification
    if not current_app.config.get('RECEIPT_DEBUGGING'):
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
        if 'invoice_id' in payment else None
    if ticket:
        if Decimal(payment['amount']) == (ticket.amount + ticket.fee):
            # Make sure the ticket in question is in the right status;
            # otherwise something is wrong, and we are in danger of asking
            # the same payment to be sent twice.
            if not ticket.status in ('received', 'quoted'):
                raise RuntimeError("Ticket was already processed: %s" % ticket.id)

            # No matter what happens, we can record that we have indeed
            # received the payment, so the user will not have any doubt
            # about that; mo matter an error that may occur later.
            ticket.ripple_address = payment['sender']
            ticket.status = 'received'
            db.session.commit()

            # Call the SEPA backend
            if current_app.config['SEPA_API']:
                # Set the status to "sending" before handing it off to the
                # backend API; this is because we don't trust the backend
                # to be idempotent; we cannot risk that us crashing right
                # after the backend call could lead to duplicate transfers.
                ticket.status = 'sending'
                db.session.commit()

                error = None
                try:
                    result = requests.post(current_app.config['SEPA_API'],
                                           data=json.dumps({
                        'id': ticket.id[:35],
                        'name': ticket.recipient_name,
                        'bic': ticket.bic,
                        'iban': ticket.iban,
                        'amount': format(ticket.amount, ',.2f'),
                        'text': 'sepa.link: %s' % ticket.text,
                        'verify': tx_hash
                    }), headers={
                        'Content-type': 'application/json',
                        'Authorization': current_app.config['SEPA_API_AUTH']})
                except RequestException as e:
                    error = '%s' % e
                else:
                    if result.status_code != 200:
                        error =  "Unexpected status code: %s" % result.status_code

                    elif 'error' in result.json():
                        error = 'Backend did not accept transfer: %s' % \
                            result.json()['error']

                if error:
                    # We verifiably did not submit, remove the sending state.
                    ticket.status = 'received'
                    db.session.commit()
                    # Make sure we don't accept the notification
                    raise ValueError(error)

                else:
                    ticket.status = 'sent'
                    db.session.commit()

                    send_mail(
                        'SEPA bridge: Transaction processed',
                        render_template('transfer.txt', **{'ticket': ticket}))

            # If no backend is configured, only send email.
            else:
                send_mail(
                    'SEPA bridge: Payment received: Execute a transfer',
                    render_template('transfer.txt', **{'ticket': ticket}))

            # Ticket was processed successfully, forget sensitive data
            # and drop the notification.
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


