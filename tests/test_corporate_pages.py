from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, Mock

from flask import Flask
from app.extensions import db, mail
from app.models import AdminUser, AppUser, CallSession, CorporatePage, CorporateMembership, CorporateRegistrationCode, Hero
from app.routes import main_bp
from app.services.billing import build_user_access_state
from app.realtime import SocketBridge


class CorporatePagesTests(TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(SECRET_KEY='tests', TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False,
            TRY_CALLS_NUMBER=0, PUBLIC_BASE_URL='https://example.test', REALTIME_API_PROVIDER='openai',
            EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS=0, EMAIL_VERIFICATION_CODE_TTL_MINUTES=10, MAIL_SUPPRESS_SEND=True, MAIL_DEFAULT_SENDER='test@example.test')
        db.init_app(self.app)
        mail.init_app(self.app)
        self.app.register_blueprint(main_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        admin = AdminUser(username='admin', is_active=True)
        admin.set_password('test')
        hero = Hero(slug='santa', name='Дед Мороз', is_active=True)
        other = Hero(slug='other', name='Другой', is_active=True)
        db.session.add_all([admin, hero, other])
        db.session.flush()
        page = CorporatePage(endpoint='acme-holiday', name='ACME', title='Новогоднее чудо', button_label='Позвонить Деду Морозу', hero_id=hero.id, email_domains='acme.test')
        db.session.add(page)
        db.session.commit()
        self.page_id, self.admin_id, self.hero_id = page.id, admin.id, hero.id
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def csrf(self, client=None):
        client = client or self.client
        client.get('/acme-holiday')
        with client.session_transaction() as state:
            return state['corporate_csrf']

    def post(self, data, client=None):
        client = client or self.client
        return client.post('/acme-holiday', data={**data, 'csrf_token': self.csrf(client)})

    def create_code(self, raw='ABCDEF0123456789'):
        code = CorporateRegistrationCode(page_id=self.page_id, code_hash=hashlib.sha256(raw.encode()).hexdigest(), label=raw[-4:])
        db.session.add(code)
        db.session.commit()
        return raw

    def redeem(self):
        return self.post(dict(action='code', registration_code=self.create_code(), name='Иван', consent='on'))

    def test_public_branding_and_admin_protection(self):
        response = self.client.get('/acme-holiday')
        self.assertIn('Новогоднее чудо', response.text)
        self.assertNotIn('web-sidebar', response.text)
        self.assertEqual(self.client.get('/admin/corporate-pages').status_code, 302)
        self.assertEqual(self.client.get('/unknown-company').status_code, 404)
        self.assertEqual(self.client.post('/acme-holiday', data={'action': 'code'}).status_code, 400)

    def test_code_registration_is_single_use_and_scoped(self):
        self.assertEqual(self.redeem().status_code, 302)
        self.assertIn('id="mic-btn"', self.client.get('/acme-holiday').text)
        self.assertEqual(CorporateMembership.query.count(), 1)
        self.assertIsNotNone(CorporateRegistrationCode.query.one().used_at)
        other_client = self.app.test_client()
        response = self.post(dict(action='code', registration_code='ABCDEF0123456789', name='Другой', consent='on'), other_client)
        self.assertIn('уже использован', response.text)
        self.assertEqual(AppUser.query.count(), 1)
        response = self.client.post('/api/call-sessions/start', json={'character_slug': 'santa', 'corporate_page_id': self.page_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('?corporate_call=', response.json['websocket_url'])
        self.assertEqual(CallSession.query.one().corporate_page_id, self.page_id)
        self.assertEqual(self.client.post('/api/call-sessions/start', json={'character_slug': 'other', 'corporate_page_id': self.page_id}).status_code, 403)
        self.assertEqual(self.client.post('/api/call-sessions/start', json={'character_slug': 'santa'}).status_code, 403)
        page = db.session.get(CorporatePage, self.page_id)
        page.is_active = False
        db.session.commit()
        self.assertEqual(self.client.get('/acme-holiday').status_code, 404)
        self.assertEqual(self.client.post('/api/call-sessions/start', json={'character_slug': 'santa', 'corporate_page_id': self.page_id}).status_code, 403)

    def test_elevenlabs_corporate_call_uses_existing_provider(self):
        self.redeem()
        self.app.config["ELEVEN_LABS_API_KEY"] = "test-key"
        with patch('app.routes._hero_provider', return_value='elevenlabs'), patch('app.routes._resolve_elevenlabs_agent_id', return_value='agent'), patch('app.routes._sync_elevenlabs_agent_for_character'), patch('app.routes.get_signed_url', return_value='wss://example.test/signed'):
            response = self.client.post('/api/call-sessions/start', json={'character_slug':'santa', 'corporate_page_id':self.page_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json['signed_url'], 'wss://example.test/signed')
        self.assertEqual(CallSession.query.one().corporate_page_id, self.page_id)

    def test_membership_does_not_grant_other_company(self):
        self.redeem()
        page = CorporatePage(endpoint='other-company', name='Other', title='Other', button_label='Call', hero_id=self.hero_id)
        db.session.add(page)
        db.session.commit()
        self.assertNotIn('id="mic-btn"', self.client.get('/other-company').text)
        self.assertEqual(self.client.post('/api/call-sessions/start', json={'character_slug': 'santa', 'corporate_page_id': page.id}).status_code, 403)

    def test_email_requires_domain_and_ownership(self):
        response = self.post(dict(action='email', name='Иван', email='ivan@acme.test.evil.com', consent='on'))
        self.assertIn('разрешённого домена', response.text)
        with patch('app.services.email_auth.send_email_code') as send:
            response = self.post(dict(action='email', name='Иван', email='ivan@acme.test', consent='on'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CorporateMembership.query.count(), 0)
        code = send.call_args.kwargs['code']
        self.assertIn('Неверный код', self.post(dict(action='verify', email_code='bad')).text)
        response = self.post(dict(action='verify', email_code=code))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AppUser.query.one().email_verified)
        self.assertEqual(CorporateMembership.query.one().source, 'email')

    def test_email_attempts_are_limited(self):
        with patch('app.services.email_auth.send_email_code'):
            self.post(dict(action='email', name='Иван', email='ivan@acme.test', consent='on'))
        for _ in range(5):
            self.post(dict(action='verify', email_code='bad'))
        self.assertIn('Лимит попыток', self.post(dict(action='verify', email_code='bad')).text)
        self.assertEqual(CorporateMembership.query.count(), 0)

    def test_corporate_calls_do_not_consume_personal_minutes(self):
        self.redeem()
        user = AppUser.query.one()
        call = CallSession(app_user_id=user.id, character_slug='santa', corporate_page_id=self.page_id, status='finished', started_at=datetime.utcnow()-timedelta(minutes=10), ended_at=datetime.utcnow())
        db.session.add(call)
        db.session.commit()
        state = build_user_access_state(user, trial_minutes_limit=3)
        self.assertEqual(state['trial_remaining_minutes'], 3)
        self.assertFalse(state['trial_calls_tried'])

    def test_admin_create_edit_codes_and_revoke(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        token = self.csrf()
        response = self.client.post('/admin/corporate-pages', data=dict(csrf_token=token, name='New', endpoint='/new-company', title='Праздник', button_label='Звонок', hero_id=self.hero_id, email_domains='@acme.test; example.com', is_active='on'))
        self.assertEqual(response.status_code, 302)
        page = CorporatePage.query.filter_by(endpoint='new-company').one()
        self.assertEqual(page.email_domains, 'acme.test, example.com')
        self.assertEqual(self.client.get(response.location).status_code, 200)
        response = self.client.post('/admin/corporate-pages', data=dict(csrf_token=token, page_id=page.id, action='codes', count=2))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CorporateRegistrationCode.query.count(), 2)
        code = CorporateRegistrationCode.query.first()
        self.client.post('/admin/corporate-pages', data=dict(csrf_token=token, page_id=page.id, action='revoke', code_id=code.id))
        db.session.refresh(code)
        self.assertIsNotNone(code.revoked_at)
        for endpoint in ('admin', 'api', '../evil', 'web', 'static', 'acme-holiday'):
            response = self.client.post('/admin/corporate-pages', data=dict(csrf_token=token, name='X', endpoint=endpoint, title='X', button_label='X', hero_id=self.hero_id))
            self.assertEqual(response.status_code, 200)
            self.assertIn('corporate-error', response.text)

    def test_upload_rejects_svg(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        response = self.client.post('/admin/corporate-pages', data=dict(csrf_token=self.csrf(), name='X', endpoint='new-company', title='X', button_label='X', hero_id=self.hero_id, logo=(io.BytesIO(b'<svg/>'), 'logo.svg')))
        self.assertIn('PNG, JPEG или WebP', response.text)
        self.assertEqual(CorporatePage.query.count(), 1)

    def test_socket_rejects_corporate_call_without_authenticated_owner(self):
        self.redeem()
        response = self.client.post('/api/call-sessions/start', json={'character_slug':'santa', 'corporate_page_id':self.page_id})
        call_id = response.json['call_session_id']
        with self.app.test_request_context('/ws/call/santa'):
            bridge = SocketBridge(Mock(), 'santa')
            with self.assertRaisesRegex(ValueError, 'Нет доступа'):
                bridge._create_call_session({'app_user_id':AppUser.query.one().id, 'call_session_id':call_id})

    def test_admin_saves_clears_and_validates_call_instructions(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        form = dict(csrf_token=self.csrf(), page_id=self.page_id, name='ACME', endpoint='acme-holiday', title='Праздник', button_label='Звонок', hero_id=self.hero_id, is_active='on')
        instruction = 'Компания Север. Обязательно узнай имя ребёнка и его возраст.'
        response = self.client.post('/admin/corporate-pages', data={**form, 'call_instructions': instruction})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(CorporatePage, self.page_id).call_instructions, instruction)
        self.assertIn(instruction, self.client.get(response.location).text)
        self.assertNotIn(instruction, self.client.get('/acme-holiday').text)
        response = self.client.post('/admin/corporate-pages', data={**form, 'call_instructions': 'x' * 20001})
        self.assertIn('20 000', response.text)
        self.assertEqual(db.session.get(CorporatePage, self.page_id).call_instructions, instruction)
        self.client.post('/admin/corporate-pages', data={**form, 'call_instructions': ''})
        self.assertEqual(db.session.get(CorporatePage, self.page_id).call_instructions, '')

    def test_openai_session_uses_snapshot_and_keeps_base_prompt(self):
        from flask import session
        from app.characters import build_realtime_session_config, get_character
        page = db.session.get(CorporatePage, self.page_id)
        page.call_instructions = 'Компания Север. Узнай возраст.'
        hero = db.session.get(Hero, self.hero_id)
        hero.system_prompt = 'Основной образ Деда Мороза.'
        hero.realtime_settings_json = {'instructions_override': 'Основные инструкции персонажа.'}
        db.session.commit()
        self.redeem()
        response = self.client.post('/api/call-sessions/start', json={'character_slug':'santa', 'corporate_page_id':self.page_id})
        call_id = response.json['call_session_id']
        page.call_instructions = 'Новый сценарий другой компании.'
        db.session.commit()
        with self.app.test_request_context('/ws/call/santa'):
            session['app_user_id'] = AppUser.query.one().id
            bridge = SocketBridge(Mock(), 'santa', corporate_call_id=call_id)
            bridge._create_call_session({'app_user_id':session['app_user_id'], 'call_session_id':call_id})
            with patch('app.realtime.create_connection', return_value=Mock()):
                self.app.config.update(OPENAI_REALTIME_MODEL='gpt-realtime', OPENAI_REALTIME_VOICE='alloy', OPENAI_API_KEY='test')
                bridge._connect_openai()
            import json
            sent = json.loads(bridge.openai_ws.send.call_args.args[0])
            instructions = sent['session']['instructions']
            self.assertIn('Компания Север. Узнай возраст.', instructions)
            self.assertIn('Основные инструкции персонажа.', instructions)
            self.assertNotIn('Новый сценарий', instructions)
            regular = build_realtime_session_config(get_character('santa'), 'gpt-realtime', 'alloy')
            self.assertNotIn('Компания Север', regular['instructions'])
            self.assertNotIn('corporate_call_instructions', get_character('santa'))

    def test_elevenlabs_keeps_corporate_context_out_of_shared_agent(self):
        from app.routes import _build_elevenlabs_agent_payload
        page = db.session.get(CorporatePage, self.page_id)
        page.call_instructions = 'Компания Север. Спроси о любимом подарке.'
        db.session.commit()
        self.redeem()
        self.app.config['ELEVEN_LABS_API_KEY'] = 'test'
        with patch('app.routes._hero_provider', return_value='elevenlabs'), patch('app.routes._resolve_elevenlabs_agent_id', return_value='agent'), patch('app.routes.update_agent') as update, patch('app.routes.get_signed_url', return_value='wss://signed'):
            response = self.client.post('/api/call-sessions/start', json={'character_slug':'santa', 'corporate_page_id':self.page_id})
        self.assertEqual(response.status_code, 200, response.text)
        variables = response.json['conversation_initiation_client_data']['dynamic_variables']
        self.assertIn(page.call_instructions, variables['corporate_call_instructions'])
        prompt = update.call_args.kwargs['conversation_config']['agent']['prompt']['prompt']
        self.assertIn('{{corporate_call_instructions}}', prompt)
        self.assertNotIn('Компания Север', prompt)
        self.assertEqual(CallSession.query.one().meta_json['corporate_call_instructions'], page.call_instructions)
        from flask import session
        with self.app.test_request_context('/ws/call/santa'):
            session['app_user_id'] = AppUser.query.one().id
            bridge = SocketBridge(Mock(), 'santa', corporate_call_id=response.json['call_session_id'])
            self.app.config.update(OPENAI_API_KEY='test', OPENAI_CHAT_MODEL='test')
            with patch('app.realtime.generate_chat_reply', return_value='Привет') as generate:
                bridge._generate_elevenlabs_reply(greeting=True)
            self.assertIn('Компания Север', generate.call_args.kwargs['messages'][0]['content'])

    def test_elevenlabs_setup_failure_does_not_skip_corporate_scenario(self):
        page = db.session.get(CorporatePage, self.page_id)
        page.call_instructions = 'Узнай имя.'
        db.session.commit()
        self.redeem()
        with patch('app.routes._hero_provider', return_value='elevenlabs'), patch('app.routes._resolve_elevenlabs_agent_id', return_value='agent'), patch('app.routes._sync_elevenlabs_agent_for_character', side_effect=RuntimeError('provider')), patch('app.routes.get_signed_url') as signed:
            response = self.client.post('/api/call-sessions/start', json={'character_slug':'santa', 'corporate_page_id':self.page_id})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(signed.called)
        self.assertEqual(CallSession.query.one().status, 'failed')

    def test_logo_rounding_is_saved_rendered_and_validated(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        form = dict(csrf_token=self.csrf(), page_id=self.page_id, name='ACME', endpoint='acme-holiday', title='Праздник', button_label='Звонок', hero_id=self.hero_id, is_active='on')
        page = db.session.get(CorporatePage, self.page_id)
        page.logo_path = 'images/logo.webp'
        db.session.commit()
        response = self.client.post('/admin/corporate-pages', data={**form, 'logo_border_radius': 24})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(page.logo_border_radius, 24)
        self.assertIn('--corporate-logo-radius:24px', self.client.get('/acme-holiday').text)
        self.assertIn('Скругление углов', self.client.get(response.location).text)
        for invalid in ('-1', '201', '5.5', 'invalid'):
            response = self.client.post('/admin/corporate-pages', data={**form, 'logo_border_radius': invalid})
            self.assertIn('целое число от 0 до 200', response.text)
            self.assertEqual(page.logo_border_radius, 24)
        self.client.post('/admin/corporate-pages', data={**form, 'logo_border_radius': 0})
        self.assertEqual(page.logo_border_radius, 0)

    def test_corporate_font_can_be_replaced_removed_and_scoped(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        form = dict(csrf_token=self.csrf(), page_id=self.page_id, name='ACME', endpoint='acme-holiday', title='Праздник', button_label='Звонок', hero_id=self.hero_id, is_active='on')
        with patch('app.corporate._upload_font', return_value=('uploads/corporate/test.woff2', 'Brand.woff2')):
            self.assertEqual(self.client.post('/admin/corporate-pages', data=form).status_code, 302)
        page = db.session.get(CorporatePage, self.page_id)
        self.assertEqual(page.font_original_name, 'Brand.woff2')
        html = self.client.get('/acme-holiday').text
        self.assertIn('@font-face', html)
        self.assertIn('corporate-custom-font', html)
        self.assertIn('/static/uploads/corporate/test.woff2', html)
        other = CorporatePage(endpoint='other-brand', name='Other', title='Other', button_label='Call', hero_id=self.hero_id)
        db.session.add(other)
        db.session.commit()
        self.assertNotIn('@font-face', self.client.get('/other-brand').text)
        self.client.post('/admin/corporate-pages', data=form)
        self.assertEqual(page.font_original_name, 'Brand.woff2')
        with patch('app.corporate._upload_font', return_value=('uploads/corporate/next.ttf', 'Next.ttf')):
            self.client.post('/admin/corporate-pages', data=form)
        self.assertEqual(page.font_original_name, 'Next.ttf')
        self.client.post('/admin/corporate-pages', data={**form, 'remove_font': 'on'})
        self.assertIsNone(page.font_path)
        self.assertIsNone(page.font_original_name)
        self.assertNotIn('@font-face', self.client.get('/acme-holiday').text)

    def test_font_upload_rejects_invalid_or_oversized_files(self):
        with self.client.session_transaction() as state:
            state['admin_user_id'] = self.admin_id
        form = dict(csrf_token=self.csrf(), page_id=self.page_id, name='ACME', endpoint='acme-holiday', title='Праздник', button_label='Звонок', hero_id=self.hero_id)
        for filename, content in [('font.svg', b'<svg/>'), ('font.woff2', b'<script>alert(1)</script>'), ('font.ttf', b'\x00\x01\x00\x00'), ('font.woff', b'wOFF')]:
            response = self.client.post('/admin/corporate-pages', data={**form, 'font': (io.BytesIO(content), filename)})
            self.assertIn('Загрузите файл шрифта', response.text)
            self.assertIsNone(db.session.get(CorporatePage, self.page_id).font_path)
        response = self.client.post('/admin/corporate-pages', data={**form, 'font': (io.BytesIO(b'x' * (8 * 1024 * 1024 + 1)), 'font.woff2')})
        self.assertIn('не больше 8 МБ', response.text)


class CorporateMigrationTests(TestCase):
    def test_upgrade_and_downgrade_preserve_existing_calls(self):
        import importlib.util
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/3b4c5d6e7f8a_add_corporate_pages.py'
        spec = importlib.util.spec_from_file_location('corporate_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE hero (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE app_user (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE email_code (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE call_session (id INTEGER PRIMARY KEY, status VARCHAR(32))')
            connection.exec_driver_sql("INSERT INTO call_session (id, status) VALUES (1, 'finished')")
            connection.exec_driver_sql('INSERT INTO email_code (id) VALUES (1)')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn('corporate_page', sa.inspect(connection).get_table_names())
                self.assertEqual(connection.exec_driver_sql('SELECT failed_attempts FROM email_code').scalar(), 0)
                self.assertEqual(connection.exec_driver_sql('SELECT corporate_page_id FROM call_session').scalar(), None)
                migration.downgrade()
                self.assertNotIn('corporate_page', sa.inspect(connection).get_table_names())
                self.assertEqual(connection.exec_driver_sql('SELECT status FROM call_session WHERE id=1').scalar(), 'finished')
        engine.dispose()

    def test_schema_preparation_is_idempotent_without_alembic_history(self):
        import sqlalchemy as sa
        from scripts.prepare_corporate_schema import prepare_schema
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            for table in ('hero', 'app_user', 'email_code'):
                connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE call_session (id INTEGER PRIMARY KEY, status VARCHAR(32))')
            connection.exec_driver_sql("INSERT INTO call_session VALUES (1, 'finished')")
            prepare_schema(connection)
            prepare_schema(connection)
            self.assertIn('corporate_membership', sa.inspect(connection).get_table_names())
            self.assertEqual(connection.exec_driver_sql('SELECT status FROM call_session WHERE id=1').scalar(), 'finished')
        engine.dispose()

    def test_existing_corporate_pages_receive_empty_instruction(self):
        import sqlalchemy as sa
        from scripts.prepare_corporate_schema import prepare_schema
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            for table in ('hero', 'app_user', 'email_code'):
                connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE call_session (id INTEGER PRIMARY KEY, status VARCHAR(32))')
            prepare_schema(connection)
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            with Operations(MigrationContext.configure(connection)).batch_alter_table('corporate_page') as batch:
                batch.drop_column('call_instructions')
            connection.exec_driver_sql("INSERT INTO corporate_page (id, endpoint, name, title, button_label, hero_id, email_domains, is_active, created_at) VALUES (1, 'test-company', 'Test', 'Title', 'Call', 1, '', 1, CURRENT_TIMESTAMP)")
            prepare_schema(connection)
            prepare_schema(connection)
            self.assertEqual(connection.exec_driver_sql('SELECT call_instructions FROM corporate_page WHERE id=1').scalar(), '')
        engine.dispose()

    def test_branding_upgrade_preserves_existing_page_settings(self):
        import sqlalchemy as sa
        from scripts.prepare_corporate_schema import prepare_schema
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            for table in ('hero', 'app_user', 'email_code'):
                connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE call_session (id INTEGER PRIMARY KEY)')
            prepare_schema(connection)
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            with Operations(MigrationContext.configure(connection)).batch_alter_table('corporate_page') as batch:
                for column in ('logo_border_radius', 'font_path', 'font_original_name'):
                    batch.drop_column(column)
            connection.exec_driver_sql("INSERT INTO corporate_page (id, endpoint, name, title, button_label, hero_id, email_domains, is_active, created_at, call_instructions) VALUES (1, 'test-company', 'Test', 'Title', 'Call', 1, '', 1, CURRENT_TIMESTAMP, 'Спроси имя')")
            prepare_schema(connection)
            prepare_schema(connection)
            row = connection.exec_driver_sql('SELECT logo_border_radius, font_path, font_original_name, call_instructions FROM corporate_page WHERE id=1').one()
            self.assertEqual(tuple(row), (0, None, None, 'Спроси имя'))
        engine.dispose()
