import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from web import create_app


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only',
                              'DATA_DIR': Path(cls.directory.name), 'SEED_DEMO': True})

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.client = self.app.test_client()

    def token(self, client=None):
        client = client or self.client
        client.get('/')
        with client.session_transaction() as session:
            return session['csrf']

    def post(self, url, data=None, client=None):
        client = client or self.client
        return client.post(url, data={**(data or {}), 'csrf': self.token(client)}, follow_redirects=True)

    def login(self, username='student', password='Student123', role='Student', client=None):
        return self.post('/', dict(username=username, password=password, role=role), client)

    def test_public_resources(self):
        for path in ('/', '/health', '/calendar', '/university-timetable',
                     '/university-timetable?batch=2Q1A', '/static/style.css',
                     '/assets/academic_calendar_preview.png'):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        self.assertIn(b'Batch not found', self.client.get('/university-timetable?batch=INVALID').data)

    def test_all_roles_render_and_invalid_login(self):
        for username, password, role in (('student', 'Student123', 'Student'),
                                        ('faculty', 'Faculty123', 'Admin / Faculty'),
                                        ('gate', 'Gate1234', 'Gate Staff')):
            client = self.app.test_client()
            response = self.login(username, password, role, client)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Sign out', response.data)
        self.assertIn(b'incorrect', self.login(password='bad').data)

    def test_csrf_and_role_boundaries(self):
        self.assertEqual(self.client.post('/', data={}).status_code, 400)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.login()
        self.assertEqual(self.post('/action/add-student', {'username': 'forbidden'}).status_code, 403)
        self.assertEqual(self.client.get('/gate-photo/1').status_code, 403)

    def test_certificate_ownership_and_integrity(self):
        self.login('faculty', 'Faculty123', 'Admin / Faculty')
        self.post('/action/add-student', dict(username='other', name='Other Student', password='Other1234'))
        response = self.post('/action/certificate', dict(student='student', event='Web test',
                              file=(BytesIO(b'%PDF-1.4\nTest'), 'award.pdf')))
        self.assertIn(b'Saved successfully', response.data)
        self.post('/logout')
        self.login()
        page = self.client.get('/dashboard').data
        path = re.search(rb'href="(/certificate/\d+)"', page).group(1).decode()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.post('/logout')
        self.login('other', 'Other1234')
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_personal_timetable_and_identity(self):
        self.login()
        response = self.post('/action/class', dict(day='Monday', subject='OOP', instructor='Faculty', start='09:00', end='10:00'))
        self.assertIn(b'OOP', response.data)
        response = self.post('/action/class', dict(day='Monday', subject='Bad', instructor='Faculty', start='11:00', end='10:00'))
        self.assertIn(b'End time must be after', response.data)
        photo = BytesIO()
        Image.new('RGB', (32, 32), 'red').save(photo, 'PNG')
        photo.seek(0)
        response = self.post('/action/identity', dict(number='TEST-123', photo=(photo, 'portrait.png')))
        self.assertIn(b'ENROLLED', response.data)
        self.assertEqual(self.client.get('/identity-photo').status_code, 200)
        locker = self.app.extensions['locker']
        with locker.connect() as db:
            id = db.execute('SELECT id FROM timetable WHERE subject=?', ('OOP',)).fetchone()[0]
        self.assertNotIn(b'<td>OOP</td>', self.post('/action/delete-class', dict(id=str(id))).data)

    def test_exam_and_confirmed_deletion(self):
        self.login('faculty', 'Faculty123', 'Admin / Faculty')
        self.post('/action/add-student', dict(username='temporary', name='Temporary', password='Temp1234'))
        response = self.post('/action/exam', dict(student='temporary', subject='OOP Exam', centre='LT101', day='2026-12-07', start='09:00', end='12:00', score='80', maximum='100'))
        self.assertIn(b'Saved successfully', response.data)
        other = self.app.test_client()
        self.assertIn(b'80 / 100', self.login('temporary', 'Temp1234', client=other).data)
        self.assertIn(b'Type the student username', self.post('/action/delete-student', dict(student='temporary', confirm='wrong')).data)
        self.post('/action/delete-student', dict(student='temporary', confirm='temporary'))
        self.assertEqual(other.get('/dashboard').status_code, 302)

    def test_no_demo_by_default_and_durable_records(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(TESTING=True, SECRET_KEY='test', DATA_DIR=directory, SEED_DEMO=False)
            app = create_app(config)
            with app.extensions['locker'].connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)
            self.login()
            second = create_app(dict(self.app.config, SEED_DEMO=False))
            with second.extensions['locker'].connect() as db:
                self.assertIsNotNone(db.execute("SELECT 1 FROM users WHERE username='student'").fetchone())

    def test_gate_checks_and_history(self):
        self.login()
        image = Image.new('RGB', (64, 64))
        image.putdata([(x * 4, y * 4, (x + y) * 2) for y in range(64) for x in range(64)])
        reference = BytesIO()
        image.save(reference, 'PNG')
        reference_bytes = reference.getvalue()
        self.post('/action/identity', dict(number='GATE-TEST', photo=(BytesIO(reference_bytes), 'reference.png')))
        staff = self.app.test_client()
        self.login('gate', 'Gate1234', 'Gate Staff', staff)
        fields = dict(student='student', direction='Entry', location='Main Gate', number='GATE-TEST')
        response = self.post('/action/gate', dict(**fields, photo=(BytesIO(reference_bytes), 'same.png')), staff)
        self.assertIn(b'Submit a different photo', response.data)
        image.putpixel((0, 0), (10, 10, 10))
        capture = BytesIO()
        image.save(capture, 'PNG')
        capture.seek(0)
        response = self.post('/action/gate', dict(**fields, photo=(capture, 'capture.png')), staff)
        self.assertIn(b'Saved successfully', response.data)
        self.assertIn(b'Main Gate', self.client.get('/dashboard').data)

    def test_login_throttle_and_upload_validation(self):
        for _ in range(10):
            self.login('missing-account', 'wrong')
        self.assertEqual(self.login('missing-account', 'wrong').status_code, 429)
        self.login('faculty', 'Faculty123', 'Admin / Faculty')
        response = self.post('/action/certificate', dict(student='student', event='Invalid',
                             file=(BytesIO(b'Not a PDF'), 'fake.pdf')))
        self.assertIn(b'Choose a PDF certificate', response.data)


if __name__ == '__main__':
    unittest.main()
