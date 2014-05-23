from decimal import Decimal
import json
from urllib.parse import parse_qsl
from flask import url_for
import responses
import pytest
from ripple.sepa import create_app
from ripple.sepa.bridge import Ticket, db
from ripple.sepa.utils import parse_sepa_data


def test_sepa_url():
    """Test the parsing of our SEPA-recipient encoding.
    """
    # Full dataset
    assert parse_sepa_data('User+Name/GB82WEST12345698765432/DABADKKK/Foo+Bar') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': 'Foo Bar'
    }

    # BIC/IBAN reversed
    assert parse_sepa_data('User+Name/DABADKKK/GB82WEST12345698765432/Foo+Bar') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': 'Foo Bar'
    }

    # No name
    assert parse_sepa_data('DABADKKK/GB82WEST12345698765432/Foo+Bar') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': '',
        'text': 'Foo Bar'
    }

    # No text
    assert parse_sepa_data('User+Name/DABADKKK/GB82WEST12345698765432') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': 'User Name',
        'text': ''
    }

    # Neither name of text
    assert parse_sepa_data('DABADKKK/GB82WEST12345698765432') == {
        'iban': 'GB82WEST12345698765432',
        'bic': 'DABADKKK',
        'name': '',
        'text': ''
    }

    # Invalid IBAN
    with pytest.raises(ValueError):
        parse_sepa_data('DABADKKK/GB82WEST12345691765432')

    # Invalid BIC
    with pytest.raises(ValueError):
        parse_sepa_data('DABADKKKa/GB82WEST12345698765432')

    # No IBAN
    with pytest.raises(ValueError):
        parse_sepa_data('User/DABADKKK/Text')
    # No BIC
    with pytest.raises(ValueError):
        parse_sepa_data('User/GB82WEST12345698765432/Text')


@pytest.fixture
def app():
    return create_app(config={
        'SERVER_NAME': 'testinghost',
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///',
        'SEPA_API': 'http://sepa/'
    })


@pytest.fixture
def client(request, app):
    ctx = app.app_context()
    ctx.push()

    def teardown():
        ctx.pop()
    request.addfinalizer(teardown)

    return app.test_client()


class TestBridgeAPI:
    """The API we provide to Ripple clients.
    """

    def test_federation(self, client):
        """Test the Ripple federation view.
        """

        # Test a request with incorrectly formatted SEPA recipient.
        response = client.get(url_for('site.federation'), query_string={
            'type': 'federation', 'domain': 'testinghost', 'destination': 'foo'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']

        # Test a request with proper SEPA recipient.
        response = client.get(url_for('site.federation'), query_string={
            'type': 'federation', 'domain': 'testinghost',
            'destination': 'DABADKKK/GB82WEST12345698765432'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert 'quote' in result['federation_json']['quote_url']

    def test_quote(self, client):
        """Test the Ripple quote view.
        """

        # Test a request with incorrectly formatted SEPA recipient.
        response = client.get(url_for('site.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'destination': '', 'amount': '22.00/EUR'})
        assert response.status_code == 200
        result = json.loads(response.data.decode('utf8'))
        assert result['error']
        assert not Ticket.query.all()

        # Test a successful quote request
        response = client.get(url_for('site.quote'), query_string={
            'type': 'quote', 'domain': 'testinghost',
            'destination': 'User/DABADKKK/GB82WEST12345698765432/Text',
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
            body='OK', status=200)

        responses.start()
        def done():
            responses.stop()
            responses.reset()
        request.addfinalizer(done)

    def wasipaid_tx(self, amount, currency, invoice_id=None):
        # A notification that wasipaid might send.
        return json.dumps({
           'transaction': {'TransactionHash': 'foo'},
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
            url_for('site.on_payment_received'),
            data=self.wasipaid_tx('110', 'EUR', invoice_id=ticket.id),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate the call to the SEPA API
        assert len(responses.calls) == 2
        data_sent = dict(parse_qsl(responses.calls[1].request.body, True))
        assert data_sent['name'] == 'A User'
        assert data_sent['iban'] == 'IBAN'
        assert data_sent['bic'] == 'BIC'
        assert data_sent['text'] == 'Yadda'

        # We have deleted the bank info, updated the ticket status and
        # assigned the sending ripple address.
        assert ticket.status == 'received'
        assert ticket.ripple_address == 'rsender'
        assert ticket.iban == ''
        assert ticket.bic == ''
        assert ticket.text == ''
        assert ticket.recipient_name == ''

    def test_incorrect_payment(self, client):
        """Send a payment with the incorrect amount."""

        ticket = self.create_ticket()

        # Fake a payment for this ticket
        response = client.post(
            url_for('site.on_payment_received'),
            data=self.wasipaid_tx('50', 'XRP', invoice_id=ticket.id),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate only wasipaid was called, not the SEPA API
        assert len(responses.calls) == 1

        # Test that the ticket has been marked as failed
        assert ticket.failed == 'unexpected'

        # XXX test postmark call

    def test_incorrect_ticket(self, client):
        """Assume a payment that has no matching ticket."""
        response = client.post(
            url_for('site.on_payment_received'),
            data=self.wasipaid_tx('110', 'EUR', invoice_id=None),
            content_type='application/json')
        assert response.status_code == 200
        assert response.data == b'OK'

        # Validate only wasipaid was called, not the SEPA API
        assert len(responses.calls) == 1

        # XXX Test that an email was sent to postmark


