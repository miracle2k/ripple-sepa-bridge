# gunicorn greenlet/eventlet do not support Python 3, use uwsgi instead.
#web: uwsgi uwsgi.ini
web: gunicorn -t 99999 --max-requests 60 wsgi:app --workers 6
