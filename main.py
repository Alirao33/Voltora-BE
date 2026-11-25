"""Voltora backend entrypoint.

Run : python main.py            (http://127.0.0.1:8000, Swagger UI at /docs)
Jobs: python main.py job daily_routes | sweep
MQTT: python main.py mqtt
"""
from application import create_app

app = create_app()

if __name__ == "__main__":
    from cli import main

    main()
