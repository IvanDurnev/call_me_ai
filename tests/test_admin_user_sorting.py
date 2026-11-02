from contextlib import contextmanager
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask, template_rendered
from sqlalchemy import event

from app.extensions import db
from app.models import AdminUser, AppUser, CallSession, PricingPlan, SubscriptionPurchase
from app.routes import ADMIN_SESSION_KEY, main_bp


class AdminUserSortingTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False, TRY_CALLS_NUMBER=1)
        db.init_app(self.app)
        self.app.register_blueprint(main_bp)
        self.now = datetime(2026, 11, 2, 12)
        self.clock = patch('app.services.admin_users.datetime')
        self.clock.start().utcnow.return_value = self.now
        with self.app.app_context():
            db.create_all()
            admin = AdminUser(username='admin', is_active=True)
            admin.set_password('test')
            db.session.add(admin)
            for i, name in enumerate(['Анна', 'Борис', 'Вера', 'Глеб', 'Дина']):
                db.session.add(AppUser(name=name, email=f'user{i}@example.test', phone=f'+7000000000{i}',
                    email_verified=i==0, consent_to_personal_data=True, created_at=self.now-timedelta(days=10-i),
                    telegram_user_id=100-i if i in {0,1} else None, max_user_id=200+i if i in {0,1} else None))
            db.session.add_all([
                PricingPlan(code='unlimited', name='Безлимит', kind='unlimited', price=100, period_days=30),
                PricingPlan(code='package', name='Пакет', kind='call_package', price=100, calls_included=3),
            ])
            db.session.flush()
            users = AppUser.query.order_by(AppUser.id).all()
            self.ids = [u.id for u in users]
            self.admin_id = admin.id
            for i, code, days in [(1,'unlimited',1),(2,'unlimited',60),(3,'package',1)]:
                db.session.add(SubscriptionPurchase(app_user_id=users[i].id, invoice_id=f'paid-{i}',
                    plan_code=code, plan_name='Безлимит' if code=='unlimited' else 'Пакет', amount=100,
                    status='paid', paid_at=self.now-timedelta(days=days), currency='RUB',
                    cloudpayments_token='test-token', subscription_status='Canceled' if i==1 else 'Active'))
            # A sub-minute trial counts as tried; a call during unlimited access does not.
            for i, seconds, status in [(0,20,'finished'),(1,20,'finished'),(2,20,'failed'),(3,120,'finished')]:
                end=self.now-timedelta(hours=1)
                db.session.add(CallSession(app_user_id=users[i].id, character_slug='hero', status=status,
                                          started_at=end-timedelta(seconds=seconds), ended_at=end))
            db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session[ADMIN_SESSION_KEY] = self.admin_id

    def tearDown(self):
        self.clock.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def get_page(self, query=''):
        captured = []
        def record(sender, template, context, **extra): captured.append(context)
        template_rendered.connect(record, self.app)
        try:
            response = self.client.get('/admin/users'+query)
        finally:
            template_rendered.disconnect(record, self.app)
        self.assertEqual(response.status_code, 200)
        return response, captured[0]

    def test_trial_payment_and_subscription_states(self):
        response, context = self.get_page()
        summaries = context['summaries']
        self.assertTrue(summaries[self.ids[0]]['trial_calls_tried'])
        self.assertFalse(summaries[self.ids[0]]['has_paid'])
        self.assertFalse(summaries[self.ids[1]]['trial_calls_tried'])
        self.assertTrue(summaries[self.ids[1]]['has_paid'])
        self.assertEqual(summaries[self.ids[1]]['subscription_status'], 'Активна, автопродление отключено')
        self.assertFalse(summaries[self.ids[2]]['trial_calls_tried'])
        self.assertEqual(summaries[self.ids[2]]['subscription_status'], 'Истекла')
        self.assertTrue(summaries[self.ids[3]]['trial_calls_tried'])
        self.assertEqual(summaries[self.ids[3]]['subscription_status'], 'Пакет минут активен')
        self.assertEqual(summaries[self.ids[4]]['subscription_status'], 'Нет подписки')
        self.assertIn('Оплачивали подписку',response.get_data(as_text=True))

    def test_sorting_is_global_before_pagination_and_persists_in_links(self):
        response, context = self.get_page('?sort=name&direction=asc&per_page=2&page=2')
        self.assertEqual([u.name for u in context['users']], ['Вера','Глеб'])
        html=unescape(response.get_data(as_text=True))
        self.assertIn('sort=name&direction=asc',html)
        self.assertIn('aria-sort="ascending"',html)
        _, context = self.get_page('?sort=name&direction=desc&per_page=2')
        self.assertEqual([u.name for u in context['users']], ['Дина','Глеб'])

    def test_all_columns_support_both_directions(self):
        keys=['id','name','email','phone','email_verified','telegram','max','trial_calls_tried','has_paid','subscription_status','created_at']
        for key in keys:
            for direction in ['asc','desc']:
                with self.subTest(key=key,direction=direction):
                    _, context=self.get_page(f'?sort={key}&direction={direction}')
                    self.assertEqual((context['sort'],context['direction']),(key,direction))
        _, context=self.get_page('?sort=has_paid&direction=desc&per_page=2')
        self.assertTrue(all(context['summaries'][u.id]['has_paid'] for u in context['users']))
        _, context=self.get_page('?sort=telegram&direction=desc&per_page=1')
        self.assertEqual(context['users'][0].id,self.ids[0])

    def test_invalid_sort_and_page_parameters_are_safe(self):
        _, context=self.get_page('?sort=DROP%20TABLE&direction=invalid&page=9999&per_page=2')
        self.assertEqual((context['sort'],context['direction']),('created_at','desc'))
        self.assertEqual(context['page'],3)
        self.assertEqual(len(context['users']),1)

    def test_page_uses_batched_reads_without_transcripts_or_writes(self):
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany): statements.append(statement)
        with self.app.app_context():
            engine=db.engine
            event.listen(engine,'before_cursor_execute',capture)
            try:
                self.get_page('?sort=subscription_status')
            finally:
                event.remove(engine,'before_cursor_execute',capture)
        self.assertLessEqual(len(statements),6)
        self.assertFalse(any(s.lstrip().upper().startswith(('UPDATE','INSERT','DELETE')) for s in statements))
        call_queries=[s for s in statements if 'FROM call_session' in s]
        self.assertTrue(call_queries)
        self.assertNotIn('meta_json',call_queries[0])


if __name__ == '__main__': unittest.main()
