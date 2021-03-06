from decimal import Decimal
import json
from unittest import mock
from flask import url_for, current_app
import postmark
import responses
import pytest
from ripple.sepa import create_app
from ripple.sepa.bridge import Ticket, db
from ripple.sepa.utils import parse_sepa_destination, validate_sepa


def test_sepa_url():
    """Test the parsing of our SEPA-recipient encoding.
    """

    p = parse_sepa_destination

    # Full dataset
    assert p('User+Name/GB82WEST12345698765432/DABADKKK/Foo+Bar') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': 'Foo Bar'
    }

    # No text
    assert p('User+Name/GB82WEST12345698765432/DABADKKK') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': ''
    }

    # Base64
    assert p('VXNlciBOYW1lL0dCODJXRVNUMTIzNDU2OTg3NjU0MzIvREFCQURLS0s=') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': ''
    }

    # Special case: an unencoded /../../ string decode as valid b64
    assert p('User Name/GB82WEST12345698765432/DABADKKK') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': ''
    }

def test_sepa_validate():
    """Test SEPA dataset validation."""

    # Invalid IBAN
    with pytest.raises(ValueError):
        validate_sepa({'bic': 'DABADKKK', 'iban': 'GB82WEST12345691765432',
                       'name': 'foo', 'text': 'bar'})

    # Invalid BIC
    with pytest.raises(ValueError):
        validate_sepa({'bic': 'DABADKKKa', 'iban': 'GB82WEST12345698765432',
                       'name': 'foo', 'text': 'bar'})

    # No name
    with pytest.raises(ValueError):
        validate_sepa({'bic': 'DABADKKK', 'iban': 'GB82WEST12345698765432',
                       'name': '', 'text': 'bar'})

    # Text too long
    with pytest.raises(ValueError):
        validate_sepa({'bic': 'DABADKKK', 'iban': 'GB82WEST12345698765432',
                       'name': '', 'text': 'b'*140})


@pytest.fixture
def app(request):
    app = create_app(config={
        'SERVER_NAME': 'testinghost',
        'TESTING': True,
        'DEBUG': True, # disables SSLify
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///',
        'SEPA_API': 'http://sepa/',
        'BRIDGE_ADDRESS': 'rNrvihhhjDu6xmAzJBiKmEZDkjdYufh8s4',
        'ACCEPTED_ISSUERS': ['rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B'],
        'POSTMARK_KEY': 'foobar',
        'POSTMARK_SENDER': 'admin@foo.bar',
        'ADMINS': ['foo@example.org'],
    })

    ctx = app.app_context()
    ctx.push()
    def teardown():
        ctx.pop()
    request.addfinalizer(teardown)

    return app


@pytest.fixture
def client(request, app):
    return app.test_client()


class TestBridgeAPI:
    """The API we provide to Ripple clients.
    """

    def test_ripple_txt(self, client):
        """Make sure ripple.txt can be viewed."""
        response = client.get(url_for('bridge.ripple_txt'))
        assert response.status_code == 200

    def test_index(self, client):
        """Make sure index can be viewed."""
        response = client.get(url_for('bridge.index'))
        assert response.status_code == 200

    def test_federation(self, client):
        """Test the Ripple federation view.
        """

        # Test a request with incorrectly formatted SEPA recipient.
        response = client.get(url_for('bridge.federation'), query_string={
            'type': 'federation', 'domain': 'testinghost', 'destination': 'foo'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert 'quote' in result['federation_json']['quote_url']
        assert result['federation_json']['extra_fields'][0]['value'] == ''
        assert result['federation_json']['extra_fields'][1]['value'] == ''
        assert result['federation_json']['extra_fields'][2]['value'] == ''
        assert result['federation_json']['extra_fields'][3]['value'] == ''

        # Test a request with proper SEPA recipient.
        response = client.get(url_for('bridge.federation'), query_string={
            'type': 'federation', 'domain': 'testinghost',
            'destination': 'M/i/b/f'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert 'quote' in result['federation_json']['quote_url']
        assert result['federation_json']['extra_fields'][0]['value'] == 'M'
        assert result['federation_json']['extra_fields'][1]['value'] == 'i'
        assert result['federation_json']['extra_fields'][2]['value'] == 'b'
        assert result['federation_json']['extra_fields'][3]['value'] == 'f'

    def test_quote(self, client):
        """Test the Ripple quote view.
        """

        # Test a request with missing SEPA fields.
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'amount': '22.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']
        assert not Ticket.query.all()

        # Test a successful quote request
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '22.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))

        # This will have created a ticket
        tickets = Ticket.query.all()
        assert len(tickets) == 1
        assert tickets[0].iban == 'GB82WEST12345698765432'
        assert tickets[0].bic == 'DABADKKK'
        assert tickets[0].recipient_name == 'User'
        assert tickets[0].text == 'Text'
        assert tickets[0].id == result['quote']['invoice_id']
        assert tickets[0].amount + tickets[0].fee == \
               Decimal(result['quote']['send'][0]['value'])

    def test_quote_amount(self, client):
        # Test a request with incorrectly formatted amount.
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'destination': 'User/DABADKKK/GB82WEST12345698765432/Text',
            'amount': '100.88000009/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']
        assert not Ticket.query.all()

    def test_quoted_issuers(self, client):
        """Test the ACCEPTED_ISSUERS configuration."""
        current_app.config['ACCEPTED_ISSUERS'] = ['a', 'b', 'c']
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '22.00/EUR'})
        result = json.loads(response.data.decode('utf8'))
        assert len(result['quote']['send']) == 3
        assert result['quote']['send'][0]['issuer'] == 'a'

        # If no accepted issuers are given, the bridge address is used,
        # which causes any issuer to be allowed.
        current_app.config['BRIDGE_ADDRESS'] = 'foobar'
        current_app.config['ACCEPTED_ISSUERS'] = []
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '22.00/EUR'})
        result = json.loads(response.data.decode('utf8'))
        assert len(result['quote']['send']) == 1
        assert result['quote']['send'][0]['issuer'] == 'foobar'


class TestWasIPaidNotifications:
    """Test incoming payment notifications on the bridge account."""

    @pytest.fixture(autouse=True)
    def mock_requests(self, request, app):
        # Mock wasipaid validation results
        responses.add(
            responses.POST, 'https://wasipaid.com/receipt',
            body='VALID', status=200)
        # Mock the SEPA backend
        responses.add(
            responses.POST, app.config['SEPA_API'],
            body='{"success": true}', status=200)

        responses.start()
        def done():
            responses.stop()
            responses.reset()
        request.addfinalizer(done)

    @pytest.fixture(autouse=True)
    def mock_postmark(self, request, app):
        patcher = mock.patch.object(postmark.PMMail, 'send')
        patcher.start()
        self.postmark_send = patcher
        request.addfinalizer(patcher.stop)

    def wasipaid_tx(self, amount, currency, invoice_id=None):
        # A notification that wasipaid might send.
        return json.dumps({
           'transaction': {'hash': 'foo'},
           'ledger': {},
           'data': {
               'sender': 'rsender',
               'destination': '',
               'amount': amount,
               'currency': currency,
               'issuer': '',
               'tag': '',
               'invoice_id': invoice_id
           }
        })

    def create_ticket(self):
        ticket = Ticket(
            amount='100', fee='10',
            name='A User', bic='BIC', iban='IBAN', text='Yadda')
        db.session.add(ticket)
        db.session.commit()
        return ticket

    def test_correct_payment(self, client):
        """Test handling of a correct payment notification.
        """
        ticket = self.create_ticket()

        # Fake a payment for this ticket
        response = client.post(
            url_for('bridge.on_payment_received'),
            data=self.wasipaid_tx('110', 'EUR', invoice_id=ticket.id),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate the call to the SEPA API
        assert len(responses.calls) == 2
        data_sent = json.loads(responses.calls[1].request.body, True)
        assert data_sent['name'] == 'A User'
        assert data_sent['iban'] == 'IBAN'
        assert data_sent['bic'] == 'BIC'
        assert data_sent['text'] == 'sepa.link: Yadda'

        # We have deleted the bank info, updated the ticket status and
        # assigned the sending ripple address.
        assert ticket.status == 'sent'
        assert ticket.ripple_address == 'rsender'
        assert ticket.iban == ''
        assert ticket.bic == ''
        assert ticket.text == ''
        assert ticket.recipient_name == ''

    # TODO: Test the SEPA_API backend call failing.

    def test_correct_payment_send_email(self, client):
        # With no SEPA backend configured, we will simply send out
        # an email for manual transfer initiation.
        current_app.config['SEPA_API'] = None
        ticket = self.create_ticket()

        # Fake a payment for this ticket
        response = client.post(
            url_for('bridge.on_payment_received'),
            data=self.wasipaid_tx('110', 'EUR', invoice_id=ticket.id),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate the call to the SEPA API
        assert len(postmark.PMMail.send.mock_calls) == 1

    def test_incorrect_payment(self, client):
        """Send a payment with the incorrect amount."""

        ticket = self.create_ticket()

        # Fake a payment for this ticket
        response = client.post(
            url_for('bridge.on_payment_received'),
            data=self.wasipaid_tx('50', 'XRP', invoice_id=ticket.id),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate only wasipaid was called, not the SEPA API
        assert len(responses.calls) == 1

        # Test that the ticket has been marked as failed
        assert ticket.failed == 'unexpected'

        # Test that an email was sent to postmark
        assert len(postmark.PMMail.send.mock_calls) == 1

    def test_incorrect_ticket(self, client):
        """Assume a payment that has no matching ticket."""
        response = client.post(
            url_for('bridge.on_payment_received'),
            data=self.wasipaid_tx('110', 'EUR', invoice_id=None),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate only wasipaid was called, not the SEPA API
        assert len(responses.calls) == 1

        # Test that an email was sent to postmark
        assert len(postmark.PMMail.send.mock_calls) == 1


class TestLimits:
    """Test transaction limit feature."""

    def create_ticket(self, status, amount, fee, failed='', iban=''):
        ticket = Ticket(amount=amount, fee=fee)
        ticket.status = status
        ticket.failed = failed
        ticket.iban = iban
        db.session.add(ticket)
        db.session.commit()
        return ticket

    def test_volume_calc(self, app):
        # No tx = 0
        assert Ticket.tx_volume_today() == 0
        # Tx in wrong state = still 0
        self.create_ticket('quoted', 100, 10)
        assert Ticket.tx_volume_today() == 0
        # Processed tx
        self.create_ticket('received', 100, 10)
        assert Ticket.tx_volume_today() == 100
        # A second one is added to the total
        self.create_ticket('received', 50, 5)
        assert Ticket.tx_volume_today() == 150
        # Tx in failed state that has been SEPA-sent is counted as well,
        # just to be safe.
        self.create_ticket('received', 50, 5, failed='unknown')
        assert Ticket.tx_volume_today() == 200

    def test_user_tx_limit(self, client):
        """This limit is applied on a per iban-basis
        """
        current_app.config['USER_TX_LIMIT'] = Decimal('100')

        # Cannot send an individual transaction larger than the limit
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '122.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']

        # Check the accumulative limit (quoted transactions are ignored)
        self.create_ticket('quoted', 99999, 10)
        self.create_ticket('received', 90, 10, iban="GB82WEST12345698765432")

        # 12 Euro is too much at this point.
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '12.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']

        # But we are able to send a different IBAN
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'CH9300762011623852957', 'text': 'Text',
            'amount': '12.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['quote']

    def test_bridge_tx_limit(self, client):
        """Make sure we stop accepting quotes when we are about to exceed
        the limit.
        """

        current_app.config['BRIDGE_TX_LIMIT'] = Decimal('100')
        self.create_ticket('received', 90, 10)

        # We are unable to process 12 euros
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '12.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']

        # But 9 Euro is fine - quoted transactions are ignored
        self.create_ticket('quoted', 99999, 10)
        response = client.get(url_for('bridge.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'name': 'User', 'bic': 'DABADKKK',
            'iban': 'GB82WEST12345698765432', 'text': 'Text',
            'amount': '9.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['quote']
