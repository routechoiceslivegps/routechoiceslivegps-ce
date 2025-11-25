wsgi_app = "routechoices.wsgi:application"
preload_app = True
daemon = False
raw_env = ["DJANGO_SETTINGS_MODULE=routechoices.settings"]
workers = 3
threads = 3
max_requests = 3000
max_requests_jitter = 100
