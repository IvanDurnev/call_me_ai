from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from app.characters import normalize_realtime_settings
from app.extensions import db
from app.models import AdminUser, AppUser, Hero
from app.routes import ADMIN_SESSION_KEY, APP_USER_SESSION_KEY, main_bp
from app.services.llm import generate_chat_reply
from app.services.openai_models import list_openai_models, supports_text_chat


class TextChatModelTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False, OPENAI_API_KEY='test-key',
                               OPENAI_CHAT_MODEL='gpt-4o-mini', REALTIME_API_PROVIDER='openai')
        db.init_app(self.app)
        self.app.register_blueprint(main_bp)
        with self.app.app_context():
            db.create_all()
            admin = AdminUser(username='admin', is_active=True)
            admin.set_password('test')
            user = AppUser(email='text@example.test', name='Test', phone='+70000000000',
                           email_verified=True, consent_to_personal_data=True)
            db.session.add_all([admin, user, Hero(slug='hero', name='Герой', is_active=True,
                realtime_settings_json={'provider': 'openai', 'model': 'gpt-realtime', 'elevenlabs_llm': 'grok-beta'})])
            db.session.commit()
            self.admin_id, self.user_id = admin.id, user.id
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, admin=True):
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY if admin else APP_USER_SESSION_KEY] = self.admin_id if admin else self.user_id

    def test_model_catalog_is_admin_only_and_returns_available_models(self):
        self.assertEqual(self.client.get('/api/openai/models').status_code, 401)
        self.login()
        items = [{'id': 'gpt-5-mini', 'text_chat': True}, {'id': 'gpt-image-1', 'text_chat': False}]
        with patch('app.routes.list_openai_models', return_value=items) as fetch:
            response = self.client.get('/api/openai/models')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['items'], items)
        self.assertEqual(response.get_json()['default_model'], 'gpt-4o-mini')
        fetch.assert_called_once_with(api_key='test-key')

    def test_catalog_failure_is_explicit_without_exposing_credentials(self):
        self.login()
        with patch('app.routes.list_openai_models', side_effect=RuntimeError('secret-key')):
            response = self.client.get('/api/openai/models')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-key', response.get_data(as_text=True))
        self.app.config['OPENAI_API_KEY'] = ''
        self.assertEqual(self.client.get('/api/openai/models').status_code, 503)

    def test_save_partial_update_and_clear_model_preserve_call_settings(self):
        self.login()
        response = self.client.patch('/api/heroes/hero', json={'text_chat_model': 'gpt-5-mini'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['hero']['text_chat_model'], 'gpt-5-mini')
        response = self.client.patch('/api/heroes/hero', json={'description': 'Updated'})
        self.assertEqual(response.get_json()['hero']['text_chat_model'], 'gpt-5-mini')
        response = self.client.patch('/api/heroes/hero', json={'text_chat_model': ''})
        self.assertEqual(response.get_json()['hero']['text_chat_model'], '')
        settings = response.get_json()['hero']['realtime_settings']
        self.assertEqual(settings['model'], 'gpt-realtime')
        self.assertEqual(settings['elevenlabs_llm'], 'grok-beta')

    def test_model_can_be_set_on_creation_without_changing_another_hero(self):
        self.login()
        response = self.client.post('/api/heroes', json={
            'name': 'Другой герой', 'slug': 'other-hero', 'provider': 'openai', 'text_chat_model': 'gpt-4.1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['hero']['text_chat_model'], 'gpt-4.1')
        with self.app.app_context():
            self.assertNotIn('text_chat_model', Hero.query.filter_by(slug='hero').first().realtime_settings_json)

    def test_non_text_model_cannot_be_saved(self):
        self.login()
        response = self.client.patch('/api/heroes/hero', json={'text_chat_model': 'gpt-image-1'})
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertNotIn('text_chat_model', Hero.query.first().realtime_settings_json)

    def test_chat_uses_hero_model_and_falls_back_to_project_default(self):
        self.login(admin=False)
        for value, expected in [('gpt-5-mini', 'gpt-5-mini'), ('', 'gpt-4o-mini')]:
            with self.app.app_context():
                hero = Hero.query.first()
                hero.realtime_settings_json = normalize_realtime_settings({'text_chat_model': value})
                db.session.commit()
            with patch('app.routes.generate_chat_reply', return_value='Привет') as reply:
                response = self.client.post('/api/web-chat', json={
                    'character_slug': 'hero', 'messages': [{'role': 'user', 'text': 'Привет'}]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(reply.call_args.kwargs['model'], expected)


class OpenaiModelServiceTests(unittest.TestCase):
    def test_catalog_keeps_all_ids_deduplicated_with_text_classification(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.list.return_value = [SimpleNamespace(id=i) for i in ['gpt-image-1', 'gpt-5', 'gpt-5']]
        with patch('app.services.openai_models.build_openai_client') as build:
            build.return_value.with_options.return_value = client
            items = list_openai_models(api_key='test')
        self.assertEqual(items, [{'id': 'gpt-5', 'text_chat': True}, {'id': 'gpt-image-1', 'text_chat': False}])

    def test_specialised_models_are_not_selectable(self):
        for model in ['gpt-realtime', 'gpt-live-1', 'gpt-audio', 'gpt-4o-mini-transcribe',
                      'text-embedding-3-small', 'o3-deep-research', 'gpt-3.5-turbo-instruct', 'sora-2']:
            self.assertFalse(supports_text_chat(model), model)
        for model in ['gpt-4.1', 'gpt-5.5-pro', 'gpt-6-astra', 'o3', 'gpt-5-codex', 'ft:gpt-4o-mini:org:custom']:
            self.assertTrue(supports_text_chat(model), model)

    def test_legacy_chat_uses_chat_completions(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=' OK '))])
        with patch('app.services.llm.build_openai_client', return_value=client):
            self.assertEqual(generate_chat_reply(api_key='test', model='gpt-4o-mini', messages=[{'role':'user','content':'Hi'}]), 'OK')
        client.responses.create.assert_not_called()

    def test_search_models_use_chat_completions_without_temperature(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))])
        with patch('app.services.llm.build_openai_client', return_value=client):
            generate_chat_reply(api_key='test', model='gpt-5-search-api', messages=[{'role':'user','content':'Hi'}])
        self.assertNotIn('temperature', client.chat.completions.create.call_args.kwargs)
        client.responses.create.assert_not_called()

    def test_modern_models_use_responses_without_temperature_or_storage(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=' OK ')
        with patch('app.services.llm.build_openai_client', return_value=client):
            self.assertEqual(generate_chat_reply(api_key='test', model='gpt-5-mini', messages=[{'role':'user','content':'Hi'}]), 'OK')
        client.responses.create.assert_called_once_with(model='gpt-5-mini', input=[{'role':'user','content':'Hi'}], store=False)
        client.chat.completions.create.assert_not_called()
