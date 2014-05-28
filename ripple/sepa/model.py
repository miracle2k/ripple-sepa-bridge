import binascii
from datetime import datetime, timedelta
from decimal import Decimal
import os
from flask.ext.sqlalchemy import SQLAlchemy
import sqlalchemy


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
    def tx_volume_today(cls, iban=None):
        """Determine the volume handled by the bridge today.
        """
        today = datetime.utcnow().date()
        query = (db.session
            .query(sqlalchemy.sql.func.sum(Ticket.amount))
            # Ignore quotes for which no payment was received
            .filter(Ticket.status!='quoted')
            # Only look at tickets from today
            .filter(sqlalchemy.func.date(Ticket.created_at) == today)
            # This does not work in SQLite, see:
            #   http://stackoverflow.com/questions/17333014/convert-selected-datetime-to-date-in-sqlalchemy#comment25152032_17334055
            #   http://sqlite.1065341.n5.nabble.com/CAST-td23755.html
            #.filter(cast(Ticket.created_at, sqlalchemy.types.Date)==today)
        )
        if iban:
            query = query.filter(Ticket.iban == iban)
        volume = query.one()[0]
        return volume or Decimal('0')
