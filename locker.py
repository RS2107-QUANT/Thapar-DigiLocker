"""SQLite persistence and role-scoped operations."""

from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
import sqlite3

from models import (
    Admin, Certificate, Course, EIDCard, Exam, ExamCentre, FaceProfile, GateCheck,
    GateLog, GateStaff, Hackathon, IDVerifier, Instructor, LocationVerifier,
    MarkSheet, Marks, MAX_FILE_SIZE, PhotoVerifier, Student, TimetableEntry,
    USER_TYPES,
)


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificates (
    id INTEGER PRIMARY KEY, student TEXT NOT NULL REFERENCES users(username),
    event TEXT NOT NULL, issuer TEXT NOT NULL REFERENCES users(username),
    filename TEXT NOT NULL, content BLOB NOT NULL, fingerprint TEXT NOT NULL,
    UNIQUE (student, event, fingerprint)
);
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY, student TEXT NOT NULL REFERENCES users(username),
    subject TEXT NOT NULL, centre TEXT NOT NULL, day TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL, score REAL, maximum REAL,
    UNIQUE (student, subject, day, start)
);
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY, student TEXT NOT NULL REFERENCES users(username),
    day TEXT NOT NULL, subject TEXT NOT NULL, instructor TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL,
    UNIQUE (student, day, start)
);
CREATE TABLE IF NOT EXISTS identity (
    student TEXT PRIMARY KEY REFERENCES users(username),
    eid TEXT NOT NULL UNIQUE, reference BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_log (
    id INTEGER PRIMARY KEY, student TEXT NOT NULL REFERENCES users(username),
    direction TEXT NOT NULL, location TEXT NOT NULL, time TEXT NOT NULL,
    staff TEXT NOT NULL REFERENCES users(username), photo BLOB NOT NULL
);
"""


def required(value, label):
    value = value.strip()
    if not value:
        raise ValueError(f"Enter {label}.")
    return value


def hours(start, end):
    try:
        parsed = [time.fromisoformat(value.strip()) for value in (start, end)]
    except ValueError as exc:
        raise ValueError("Enter class or exam times in HH:MM format.") from exc
    if any(len(value.strip()) != 5 or value.strip()[2] != ":" for value in (start, end)):
        raise ValueError("Enter class or exam times in HH:MM format.")
    if parsed[0] >= parsed[1]:
        raise ValueError("End time must be after start time.")
    return tuple(value.strftime("%H:%M") for value in parsed)


def read_upload(path):
    if not path:
        raise ValueError("Choose a file.")
    with Path(path).open("rb") as file:
        content = file.read(MAX_FILE_SIZE + 1)
    if not content or len(content) > MAX_FILE_SIZE:
        raise ValueError("Choose a non-empty file up to 15 MB.")
    return content


class Locker:
    """Apply role permissions and save or load domain objects using SQLite."""

    def __init__(self, path, seed_demo=True):
        self.path = path
        # Composition: Locker creates and holds the three verifier objects.
        self.verifiers = (LocationVerifier(), IDVerifier(), PhotoVerifier())
        with self.connect() as db:
            db.executescript(SCHEMA)
            if seed_demo and not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                for cls, username, name, password in (
                    (Student, "student", "Demo Student", "Student123"),
                    (Admin, "faculty", "Demo Faculty", "Faculty123"),
                    (GateStaff, "gate", "Demo Gate Staff", "Gate1234"),
                ):
                    db.execute(
                        "INSERT INTO users VALUES (?, ?, ?, ?)",
                        (username, name, cls.role, cls.hash_password(password)),
                    )

    @contextmanager
    def connect(self):
        """Commit on success, roll back on errors, and always close the connection."""
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def require(user, *classes):
        """Reject an operation unless the user's object has an allowed role type."""
        if not isinstance(user, classes):
            raise ValueError("Your role cannot perform this action.")

    @staticmethod
    def find_student(db, username):
        row = db.execute(
            "SELECT username FROM users WHERE username = ? AND role = ?",
            (username.strip().lower(), Student.role),
        ).fetchone()
        if row is None:
            raise ValueError("Student account not found.")
        return row["username"]

    def login(self, username, password, role):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ? AND role = ?",
                (username.strip().lower(), role),
            ).fetchone()
        if row:
            # Construct the correct User subclass using the selected role.
            user = USER_TYPES[role](row["username"], row["name"], row["password_hash"])
            if user.authenticate(password):
                return user
        raise ValueError("The selected role, username or password is incorrect.")

    def students(self, user):
        self.require(user, Admin, GateStaff)
        with self.connect() as db:
            return db.execute(
                "SELECT username, name FROM users WHERE role = ? ORDER BY username",
                (Student.role,),
            ).fetchall()

    def add_student(self, user, username, name, password):
        self.require(user, Admin)
        username = required(username, "a student username").lower()
        if not username.isascii() or not all(c.isalnum() or c in "-_" for c in username):
            raise ValueError("Use letters, digits, hyphens or underscores for the username.")
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?)",
                    (username, required(name, "the student name"), Student.role,
                     Student.hash_password(password)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username is already in use.") from exc

    def delete_student(self, user, username):
        self.require(user, Admin)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            username = self.find_student(db, username)
            for table in ("certificates", "exams", "timetable", "identity", "gate_log"):
                db.execute(f"DELETE FROM {table} WHERE student = ?", (username,))
            db.execute("DELETE FROM users WHERE username = ? AND role = ?",
                       (username, Student.role))

    def upload_certificate(self, user, student, event, path):
        self.require(user, Admin)
        content = read_upload(path)
        file = Path(path)
        if file.suffix.lower() != ".pdf" or not content.startswith(b"%PDF-"):
            raise ValueError("Choose a PDF certificate.")
        with self.connect() as db:
            cert = Certificate(
                self.find_student(db, student), Hackathon(required(event, "the hackathon name")),
                user.username, file.name, content,
            )
            result = db.execute(
                """INSERT INTO certificates (student, event, issuer, filename, content, fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (student, event, fingerprint) DO NOTHING""",
                (cert.owner, cert.hackathon.name, cert.issuer, cert.filename,
                 cert.content, cert.fingerprint),
            )
            if not result.rowcount:
                raise ValueError("This certificate is already in the student's locker.")

    def certificates(self, user):
        self.require(user, Student)
        with self.connect() as db:
            return db.execute(
                """SELECT c.id, c.event, u.name AS issuer, c.filename
                   FROM certificates c JOIN users u ON c.issuer = u.username
                   WHERE c.student = ? ORDER BY c.id DESC""", (user.username,)
            ).fetchall()

    def certificate(self, user, record_id):
        self.require(user, Student)
        with self.connect() as db:
            row = db.execute(
                """SELECT c.*, u.name AS issuer_name FROM certificates c
                   JOIN users u ON c.issuer = u.username WHERE c.id = ? AND c.student = ?""",
                (record_id, user.username),
            ).fetchone()
        if not row:
            raise ValueError("Certificate not found in your locker.")
        cert = Certificate(
            row["student"], Hackathon(row["event"]), row["issuer_name"],
            row["filename"], row["content"],
        )
        if cert.fingerprint != row["fingerprint"]:
            raise ValueError("Certificate integrity check failed.")
        return cert

    def save_exam(self, user, student, subject, centre, day, start, end, score, maximum):
        self.require(user, Admin)
        try:
            exam_day = date.fromisoformat(day.strip()).isoformat()
        except ValueError as exc:
            raise ValueError("Enter an exam date in YYYY-MM-DD format.") from exc
        start, end = hours(start, end)
        subject, centre = required(subject, "the subject"), required(centre, "the exam centre")
        marks = None
        if score.strip() or maximum.strip():
            try:
                marks = Marks(float(score), float(maximum))
            except ValueError as exc:
                raise ValueError("Enter numeric marks from zero to a positive maximum.") from exc
        with self.connect() as db:
            db.execute(
                """INSERT INTO exams (student, subject, centre, day, start, end, score, maximum)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (student, subject, day, start) DO UPDATE SET
                   centre = excluded.centre, end = excluded.end,
                   score = COALESCE(excluded.score, exams.score),
                   maximum = COALESCE(excluded.maximum, exams.maximum)""",
                (self.find_student(db, student), subject, centre, exam_day, start, end,
                 marks.score if marks else None, marks.maximum if marks else None),
            )

    def exams(self, user):
        self.require(user, Student)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM exams WHERE student = ? ORDER BY day, start, subject", (user.username,)
            ).fetchall()
        result = []
        for row in rows:
            exam = Exam(
                Course(row["subject"]), ExamCentre(row["centre"]), row["day"],
                row["start"], row["end"],
            )
            sheet = None
            if row["score"] is not None:
                sheet = MarkSheet(user.username, exam, Marks(row["score"], row["maximum"]))
            result.append((exam, sheet))
        return result

    def save_class(self, user, day, subject, instructor, start, end):
        self.require(user, Student)
        if day not in DAYS:
            raise ValueError("Choose a day of the week.")
        start, end = hours(start, end)
        with self.connect() as db:
            db.execute(
                """INSERT INTO timetable (student, day, subject, instructor, start, end)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (student, day, start)
                   DO UPDATE SET subject = excluded.subject, instructor = excluded.instructor,
                   end = excluded.end""",
                (user.username, day, required(subject, "the subject"),
                 required(instructor, "the instructor"), start, end),
            )

    def timetable(self, user):
        self.require(user, Student)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM timetable WHERE student = ? ORDER BY start", (user.username,)
            ).fetchall()
        return [
            (row["id"], TimetableEntry(
                row["day"], Course(row["subject"]), Instructor(row["instructor"]),
                row["start"], row["end"],
            ))
            for row in sorted(rows, key=lambda row: DAYS.index(row["day"]))
        ]

    def delete_class(self, user, record_id):
        self.require(user, Student)
        with self.connect() as db:
            db.execute("DELETE FROM timetable WHERE id = ? AND student = ?", (record_id, user.username))

    def enroll(self, user, number, photo):
        self.require(user, Student)
        card, profile = EIDCard(user.username, number), FaceProfile(photo)
        try:
            with self.connect() as db:
                db.execute(
                    """INSERT INTO identity VALUES (?, ?, ?) ON CONFLICT (student)
                       DO UPDATE SET eid = excluded.eid, reference = excluded.reference""",
                    (user.username, card.number, profile.reference_photo),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That e-ID already belongs to another student.") from exc

    @staticmethod
    def load_identity(db, username):
        row = db.execute("SELECT * FROM identity WHERE student = ?", (username,)).fetchone()
        if row:
            return EIDCard(username, row["eid"]), FaceProfile(row["reference"])
        return None

    def identity(self, user):
        self.require(user, Student)
        with self.connect() as db:
            return self.load_identity(db, user.username)

    def verify_gate(self, user, student, direction, location, number, photo):
        self.require(user, GateStaff)
        if direction not in ("Entry", "Exit"):
            raise ValueError("Choose Entry or Exit.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            student = self.find_student(db, student)
            identity = self.load_identity(db, student)
            if not identity:
                raise ValueError("The student must enroll a reference photo and e-ID first.")
            card, profile = identity
            check = GateCheck(profile, card, photo, number, location)
            for verifier in self.verifiers:
                # Polymorphism: the same call runs each subclass's own check.
                verifier.verify(check)
            log = GateLog(
                student, direction, location, datetime.now().astimezone().isoformat(timespec="seconds"),
                user.username, photo,
            )
            db.execute(
                """INSERT INTO gate_log (student, direction, location, time, staff, photo)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (log.student, log.direction, log.location, log.time, log.staff, log.photo),
            )
        return log

    def history(self, user):
        self.require(user, Student, GateStaff)
        with self.connect() as db:
            sql = "SELECT id, student, direction, location, time, staff FROM gate_log"
            if isinstance(user, Student):
                return db.execute(sql + " WHERE student = ? ORDER BY id DESC", (user.username,)).fetchall()
            return db.execute(sql + " ORDER BY id DESC").fetchall()

    def log_photo(self, user, record_id):
        self.require(user, GateStaff)
        with self.connect() as db:
            row = db.execute("SELECT photo FROM gate_log WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("Gate log entry not found.")
        return row["photo"]

