"""نقطه‌ی ورود Phusion Passenger (cPanel «Setup Python App») — پل ASGI→WSGI.

مستندات cPanel: Passenger مقدار «Entry point» (اینجا: application) را از
«Startup file» (اینجا: passenger_wsgi.py) بارگذاری می‌کند.
FastAPI اپ ASGI است و Passenger فقط WSGI می‌فهمد ← a2wsgi پل استاندارد است.
⚠️ sys.path: مسیر ریشه‌ی اپ باید صریح اضافه شود (cwd تضمین‌شده نیست).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a2wsgi import ASGIMiddleware

from apps.api.main import app as fastapi_app

application = ASGIMiddleware(fastapi_app)
