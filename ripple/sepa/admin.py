from flask import url_for, redirect, Response, request, current_app
from flask.ext.admin import Admin, AdminIndexView, expose
from flask.ext.admin.contrib.sqla import ModelView
from markupsafe import Markup
from ripple.sepa.bridge import Ticket, db


def check_auth(username, password):
    auth = current_app.config['ADMIN_AUTH']
    if not username in auth:
        return False
    return auth[username] == password


def authenticate():
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def is_authenticated():
    auth = request.authorization
    return auth and check_auth(auth.username, auth.password)


def format_id(id):
    # http://stackoverflow.com/a/12221086/15677
    s = "<small style='display: block; font-size: 10px; line-height: 11px; word-break: break-all; word-wrap: break-word;'><wbr>%s</wbr></small>" % id
    return Markup(s)


class IndexView(AdminIndexView):
    @expose()
    def index(self):
        return redirect(url_for('ticketview.index_view'))


class TicketView(ModelView):

    def is_accessible(self):
        return is_authenticated()

    def _handle_view(self, name, *args, **kwargs):
        if not self.is_accessible():
            return authenticate()

    column_display_pk = True
    column_filters = ('status', 'failed')
    column_searchable_list = ('ripple_address', 'id', 'recipient_name', 'bic', 'iban', 'text')
    column_formatters = {
        'id': lambda v, c, m, p: format_id(m.id)
    }

admin = Admin(index_view=IndexView())
admin.add_view(TicketView(Ticket, db.session))
