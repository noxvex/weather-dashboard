release: python manage.py migrate --noinput
web: python manage.py collectstatic --noinput && gunicorn weather_dashboard.wsgi --workers 2 --threads 2 --timeout 60 --bind 0.0.0.0:$PORT
