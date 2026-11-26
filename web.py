"""Browser interface for the existing DigiLocker domain and SQLite service."""
import json
import os
import secrets
import sqlite3
import tempfile
import time
from hashlib import sha256
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from locker import DAYS, Locker
from models import Admin, GateStaff, Student, USER_TYPES, GATES, MAX_FILE_SIZE

ROOT = Path(__file__).resolve().parent


def create_app(config=None):
    app = Flask(__name__)
    data = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'), DATA_DIR=data,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=MAX_FILE_SIZE + 1024 * 1024,
        SEED_DEMO=os.environ.get('SEED_DEMO', '0') == '1',
    )
    if config:
        app.config.update(config)
    data = Path(app.config['DATA_DIR'])
    data.mkdir(parents=True, exist_ok=True)
    if not app.config['SECRET_KEY']:
        if os.environ.get('RENDER'):
            raise RuntimeError('Set SECRET_KEY before deploying.')
        key = data / '.session-key'
        if not key.exists():
            key.write_text(secrets.token_hex(32), encoding='utf-8')
        app.config['SECRET_KEY'] = key.read_text(encoding='utf-8').strip()
    locker = Locker(data / 'locker.sqlite3', seed_demo=app.config['SEED_DEMO'])
    app.extensions['locker'] = locker
    # Provision non-demo staff accounts once, without committing passwords.
    with locker.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS login_attempts (account TEXT PRIMARY KEY, failures INTEGER NOT NULL, since REAL NOT NULL)')
        for cls, prefix, default in ((Admin, 'ADMIN', 'faculty'), (GateStaff, 'GATE', 'gate')):
            password = os.environ.get(prefix + '_PASSWORD')
            username = os.environ.get(prefix + '_USERNAME', default).strip().lower()
            if password and not db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                db.execute('INSERT INTO users VALUES (?, ?, ?, ?)',
                           (username, prefix.title() + ' Staff', cls.role, cls.hash_password(password)))
    timetable = json.loads((ROOT / 'assets/timetable_index.json').read_text(encoding='utf-8'))

    @app.before_request
    def authenticate_request():
        g.user = None
        username = session.get('username')
        if username:
            with locker.connect() as db:
                row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if row and row['role'] in USER_TYPES:
                g.user = USER_TYPES[row['role']](row['username'], row['name'], row['password_hash'])
            else:
                session.clear()
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'Your form expired. Refresh the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        if g.user or request.endpoint in ('login', 'dashboard'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.context_processor
    def context():
        return dict(user=g.user, csrf=session.get('csrf'), roles=USER_TYPES, days=DAYS,
                    gates=GATES, demo=app.config['SEED_DEMO'])

    def require(*classes):
        if not g.user:
            abort(401)
        if classes and not isinstance(g.user, classes):
            abort(403)
        return g.user

    def upload(name):
        file = request.files.get(name)
        if not file or not file.filename:
            raise ValueError('Choose a file to upload.')
        content = file.read(MAX_FILE_SIZE + 1)
        if not content or len(content) > MAX_FILE_SIZE:
            raise ValueError('Choose a non-empty file up to 15 MB.')
        return file, content

    @app.get('/health')
    def health():
        with locker.connect() as db:
            db.execute('SELECT 1').fetchone()
        return {'status': 'ok'}

    @app.route('/', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            account = sha256(request.form.get('username', '').strip().lower().encode()).hexdigest()
            now = time.time()
            with locker.connect() as db:
                db.execute('DELETE FROM login_attempts WHERE since < ?', (now - 900,))
                attempt = db.execute('SELECT failures FROM login_attempts WHERE account=?', (account,)).fetchone()
            if attempt and attempt['failures'] >= 10:
                flash('Too many sign-in attempts. Try again in 15 minutes.', 'error')
                return render_template('login.html'), 429
            try:
                user = locker.login(request.form.get('username', ''), request.form.get('password', ''), request.form.get('role', ''))
                with locker.connect() as db:
                    db.execute('DELETE FROM login_attempts WHERE account=?', (account,))
                session.clear()
                session.update(username=user.username, csrf=secrets.token_urlsafe(32))
                session.permanent = True
                return redirect(url_for('dashboard'))
            except ValueError as exc:
                with locker.connect() as db:
                    db.execute('INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(account) DO UPDATE SET failures=failures+1', (account, now))
                flash(str(exc), 'error')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/assets/<path:name>')
    def assets(name):
        return send_from_directory(ROOT / 'assets', name)

    @app.get('/university-timetable')
    def university_timetable():
        batch = request.args.get('batch', '').strip().upper()
        record = timetable['batches'].get(batch)
        return render_template('university.html', batches=sorted(timetable['batches']),
                               batch=batch, record=record, term=timetable['term'])

    @app.get('/calendar')
    def calendar():
        return render_template('calendar.html')

    @app.get('/dashboard')
    def dashboard():
        user = require()
        data = {}
        if isinstance(user, Student):
            data.update(certificates=locker.certificates(user), exams=locker.exams(user),
                        timetable=locker.timetable(user), identity=locker.identity(user), history=locker.history(user))
        else:
            data['students'] = locker.students(user)
            if isinstance(user, GateStaff):
                data['history'] = locker.history(user)
        return render_template('dashboard.html', **data)

    @app.post('/action/<action>')
    def action(action):
        user = require()
        f = request.form
        try:
            if action == 'add-student':
                require(Admin)
                locker.add_student(user, f.get('username', ''), f.get('name', ''), f.get('password', ''))
            elif action == 'delete-student':
                require(Admin)
                if f.get('confirm') != f.get('student'):
                    raise ValueError('Type the student username to confirm deletion.')
                locker.delete_student(user, f.get('student', ''))
            elif action == 'certificate':
                require(Admin)
                file, content = upload('file')
                if not file.filename.lower().endswith('.pdf') or not content.startswith(b'%PDF-'):
                    raise ValueError('Choose a PDF certificate.')
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / (secure_filename(file.filename) or 'certificate.pdf')
                    path.write_bytes(content)
                    locker.upload_certificate(user, f.get('student', ''), f.get('event', ''), path)
            elif action == 'exam':
                require(Admin)
                locker.save_exam(user, *(f.get(k, '') for k in ('student', 'subject', 'centre', 'day', 'start', 'end', 'score', 'maximum')))
            elif action == 'class':
                require(Student)
                locker.save_class(user, *(f.get(k, '') for k in ('day', 'subject', 'instructor', 'start', 'end')))
            elif action == 'delete-class':
                require(Student)
                locker.delete_class(user, int(f.get('id', '0')))
            elif action == 'identity':
                require(Student)
                _, content = upload('photo')
                locker.enroll(user, f.get('number', ''), content)
            elif action == 'gate':
                require(GateStaff)
                _, content = upload('photo')
                locker.verify_gate(user, *(f.get(k, '') for k in ('student', 'direction', 'location', 'number')), content)
            else:
                abort(404)
            flash('Saved successfully.', 'success')
        except (ValueError, sqlite3.IntegrityError) as exc:
            flash(str(exc) if isinstance(exc, ValueError) else 'That record conflicts with an existing record.', 'error')
        return redirect(url_for('dashboard'))

    @app.get('/certificate/<int:record_id>')
    def certificate(record_id):
        user = require(Student)
        try:
            cert = locker.certificate(user, record_id)
        except ValueError:
            abort(404)
        return send_file(BytesIO(cert.content), mimetype='application/pdf', as_attachment=True,
                         download_name=secure_filename(cert.filename) or 'certificate.pdf')

    @app.get('/identity-photo')
    def identity_photo():
        identity = locker.identity(require(Student))
        if not identity:
            abort(404)
        content = identity[1].reference_photo
        return send_file(BytesIO(content), mimetype='image/png' if content.startswith(b'\x89PNG') else 'image/jpeg')

    @app.get('/gate-photo/<int:record_id>')
    def gate_photo(record_id):
        try:
            content = locker.log_photo(require(GateStaff), record_id)
        except ValueError:
            abort(404)
        return send_file(BytesIO(content), mimetype='image/png' if content.startswith(b'\x89PNG') else 'image/jpeg')

    @app.errorhandler(401)
    def unauthorized(error):
        return redirect(url_for('login'))

    @app.errorhandler(413)
    def too_large(error):
        return render_template('error.html', message='Upload too large. Choose a file up to 15 MB.'), 413

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')))
