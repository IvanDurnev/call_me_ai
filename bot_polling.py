from app import create_app
from app.telegram_bot import run_polling_bot


app = create_app()


if __name__ == "__main__":
    run_polling_bot(app)
