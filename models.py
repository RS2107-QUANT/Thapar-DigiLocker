"""Domain objects and the simulated gate checks for Thapar DigiLocker."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from io import BytesIO
from os import urandom

from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError


GATES = ("Main Gate", "Gate 2", "Gate 3")
MAX_FILE_SIZE = 15 * 1024 * 1024


class User:
    """Common account data and authentication shared by all three roles."""

    role = ""

    def __init__(self, username, name, password_hash):
        self.username = username
        self.name = name
        # Encapsulation: authentication uses this private, name-mangled attribute.
        self.__password_hash = password_hash

    @staticmethod
    def hash_password(password):
        if len(password) < 8:
            raise ValueError("Use a password with at least 8 characters.")
        salt = urandom(16)
        digest = pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        return f"{salt.hex()}:{digest.hex()}"

    def authenticate(self, password):
        salt, expected = self.__password_hash.split(":")
        digest = pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
        return compare_digest(digest.hex(), expected)

    def __str__(self):
        return f"{self.name} ({self.username})"


# Inheritance: these roles reuse User.__init__() and User.authenticate().
# Each changes a class attribute (role); this is not method overriding.
class Student(User):
    role = "Student"


class Admin(User):
    role = "Admin / Faculty"


class GateStaff(User):
    role = "Gate Staff"


USER_TYPES = {
    Student.role: Student,
    Admin.role: Admin,
    GateStaff.role: GateStaff,
}


# These small data classes generate __init__() and prevent normal reassignment.
@dataclass(frozen=True)
class Instructor:
    name: str

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Course:
    subject: str

    def __str__(self):
        return self.subject


@dataclass(frozen=True)
class TimetableEntry:
    # Composition (has-a): an entry contains Course and Instructor objects.
    day: str
    course: Course
    instructor: Instructor
    start: str
    end: str

    def __str__(self):
        return f"{self.day}, {self.start}-{self.end} · {self.course} · {self.instructor}"


@dataclass(frozen=True)
class Hackathon:
    name: str

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class ExamCentre:
    name: str

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Exam:
    # Composition: an exam contains a Course and an ExamCentre.
    course: Course
    centre: ExamCentre
    day: str
    start: str
    end: str

    def __str__(self):
        return f"{self.course} · {self.centre} · {self.day}, {self.start}-{self.end}"


class Marks:
    """Validate marks once and expose their values through read-only properties."""

    def __init__(self, score, maximum):
        if not 0 <= score <= maximum or not 0 < maximum < float("inf"):
            raise ValueError("Marks must be between zero and a finite, positive maximum.")
        self.__score = score
        self.__maximum = maximum

    # Encapsulation: callers read score and maximum without public setters.
    @property
    def score(self):
        return self.__score

    @property
    def maximum(self):
        return self.__maximum

    def __str__(self):
        return f"{self.score:g} / {self.maximum:g}"


class Document(ABC):
    """Abstract base for documents that share ownership and display behavior."""

    def __init__(self, owner, filename="", content=b""):
        self.owner = owner
        self.filename = filename
        self._content = content

    @property
    def content(self):
        return self._content

    @property
    def fingerprint(self):
        return sha256(self.content).hexdigest()

    # Abstraction: each concrete document must implement its own details.
    @abstractmethod
    def get_details(self):
        pass

    def display(self):
        # Polymorphism: Python selects get_details() from the actual subclass.
        return self.get_details()

    def __str__(self):
        return self.display()


class Certificate(Document):
    def __init__(self, owner, hackathon, issuer, filename, content):
        # Reuse the parent constructor for the common document attributes.
        super().__init__(owner, filename, content)
        self.hackathon = hackathon
        self.issuer = issuer

    def get_details(self):
        # Method overriding: provide the certificate version of this operation.
        return f"{self.hackathon} · Issued by {self.issuer} · {self.filename}"


class MarkSheet(Document):
    def __init__(self, owner, exam, marks):
        super().__init__(owner)
        # Composition: a marksheet contains Exam and Marks objects.
        self.exam = exam
        self.__marks = marks

    @property
    def marks(self):
        return self.__marks

    def get_details(self):
        # Method overriding: the same operation describes a marksheet here.
        return f"{self.exam.course} · {self.marks}"


class EIDCard(Document):
    def __init__(self, owner, number):
        super().__init__(owner)
        number = number.strip().upper()
        if not 3 <= len(number) <= 32 or not all(c.isalnum() or c == "-" for c in number):
            raise ValueError("Use a 3–32 character e-ID with letters, numbers or hyphens.")
        self.__number = number

    @property
    def number(self):
        return self.__number

    def get_details(self):
        # Method overriding: only show the last four e-ID characters.
        return f"e-ID ending in {self.number[-4:]}"


def open_photo(content):
    if not content or len(content) > MAX_FILE_SIZE:
        raise ValueError("Choose an image smaller than 15 MB.")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Choose a JPEG or PNG photo.")
            if image.width * image.height > 30_000_000:
                raise ValueError("Choose a photo smaller than 30 megapixels.")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The photo could not be read.") from exc


class FaceProfile:
    """Validate and hold the enrolled photo behind a read-only property."""

    def __init__(self, reference_photo):
        open_photo(reference_photo)
        self.__reference_photo = reference_photo

    @property
    def reference_photo(self):
        return self.__reference_photo

    def __str__(self):
        return "Enrolled reference photo"


@dataclass(frozen=True)
class GateLog:
    student: str
    direction: str
    location: str
    time: str
    staff: str
    photo: bytes

    def __str__(self):
        return f"{self.direction} · {self.student} · {self.location} · {self.time}"


@dataclass(frozen=True)
class GateCheck:
    # Composition: keep the profile, card and submitted details in one object.
    profile: FaceProfile
    card: EIDCard
    photo: bytes
    number: str
    location: str


class Verifier(ABC):
    """Abstract interface shared by the three different gate checks."""

    @abstractmethod
    def verify(self, check):
        """Raise ValueError when a check fails."""


# Inheritance and overriding: each verifier implements verify(check) differently.
class PhotoVerifier(Verifier):
    @staticmethod
    def signature(photo):
        # Compare centered grayscale portraits at a fixed scale for the lab simulation.
        thumb = ImageOps.fit(photo, (48, 48), Image.Resampling.LANCZOS)
        return ImageOps.autocontrast(ImageOps.grayscale(thumb), cutoff=1)

    def verify(self, check):
        reference = open_photo(check.profile.reference_photo)
        capture = open_photo(check.photo)
        if reference.size == capture.size and reference.tobytes() == capture.tobytes():
            raise ValueError("Submit a different photo from the enrolled reference.")
        reference, capture = self.signature(reference), self.signature(capture)
        if min(ImageStat.Stat(reference).stddev[0], ImageStat.Stat(capture).stddev[0]) < 18:
            raise ValueError("Use a well-lit portrait with visible detail.")
        difference = ImageStat.Stat(ImageChops.difference(reference, capture)).mean[0]
        if difference > 26:
            raise ValueError("The submitted photo did not match the enrolled reference.")


class IDVerifier(Verifier):
    def verify(self, check):
        if check.number.strip().upper() != check.card.number:
            raise ValueError("The e-ID does not match the enrolled card.")


class LocationVerifier(Verifier):
    def verify(self, check):
        if check.location not in GATES:
            raise ValueError("Choose a listed campus gate.")

