from app.webhook_server import app

if __name__ == "__main__":
    # Этот файл не запускается напрямую. Его использует Gunicorn.
    app.run()
