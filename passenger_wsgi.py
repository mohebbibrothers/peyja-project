"""نقطه‌ی ورود Phusion Passenger (cPanel Python App) — لایه‌ی سازگاری WSGI.

FastAPI اپ ASGI است؛ هاست‌های cPanel از Passenger (WSGI) استفاده می‌کنند.
a2wsgi پل استاندارد بین این دو جهان است (بدون از دست دادن endpointهای async).
"""
from a2wsgi import ASGIMiddleware

from apps.api.main import app as fastapi_app

application = ASGIMiddleware(fastapi_app)
