import logging

from routes import create_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

asgi = create_app("./dist")
