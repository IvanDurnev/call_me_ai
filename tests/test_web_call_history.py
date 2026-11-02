from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask

from app.extensions import db
from app.models import AppUser, CallSession, Hero
from app.routes import APP_USER_SESSION_KEY, main_bp


class WebCallHistoryTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False, PUBLIC_BASE_URL='http://localhost')
        db.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            user = AppUser(email='history@example.test', name='History', phone='+70000000000',
                           consent_to_personal_data=True, email_verified=True)
            other = AppUser(email='other@example.test', name='Other', phone='+70000000001',
                            consent_to_personal_data=True, email_verified=True)
            db.session.add_all([user, other])
            db.session.flush()
            self.user_id = user.id
            start = datetime(2025, 1, 1, 10)
            db.session.add_all([
                Hero(slug='hero', name='Герой', is_active=True),
                CallSession(app_user_id=user.id, character_slug='hero', status='finished', started_at=start,
                            ended_at=start + timedelta(seconds=75),
                            meta_json={'character_name': 'Герой', 'conversation_log': [{'role': 'user', 'text': 'Привет!'}]}),
                CallSession(app_user_id=other.id, character_slug='hero', meta_json={'conversation_log': [{'text': 'Чужой секрет'}]}),
                CallSession(app_user_id=user.id, character_slug='deleted-hero', status='finished',
                            meta_json={'character_name': 'Старый герой'}),
            ])
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session[APP_USER_SESSION_KEY] = self.user_id

    def test_history_requires_verified_session(self):
        self.assertEqual(self.client.get('/api/web/calls').status_code, 401)
        self.login()
        with self.app.app_context():
            db.session.get(AppUser, self.user_id).email_verified = False
            db.session.commit()
        self.assertEqual(self.client.get('/api/web/calls').status_code, 403)

    def test_history_returns_saved_calls_and_excludes_other_users(self):
        self.login()
        response = self.client.get('/api/web/calls')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        calls = response.get_json()['calls']
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['duration'], '01:15')
        self.assertEqual(calls[0]['started_at'], '2025-01-01T10:00:00Z')
        self.assertEqual(calls[0]['transcript'], [{'role': 'user', 'text': 'Привет!'}])
        self.assertNotIn('Чужой секрет', response.get_data(as_text=True))

    def test_messenger_does_not_restore_deleted_heroes_from_call_history(self):
        self.login()
        response = self.client.get('/web')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Старый герой', response.get_data(as_text=True))
        self.assertNotIn('deleted-hero', response.get_data(as_text=True))
        calls = self.client.get('/api/web/calls').get_json()['calls']
        self.assertIn('deleted-hero', [call['character_slug'] for call in calls])

    def test_account_has_no_transcripts_and_links_to_messenger(self):
        self.login()
        with patch('app.routes._sync_pending_subscription_purchases'):
            response = self.client.get('/account')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('account-transcript', html)
        self.assertNotIn('Пока нет звонков', html)
        self.assertIn('href="/web"', html)
        self.assertIn('История платежей', html)


if __name__ == '__main__':
    unittest.main()
