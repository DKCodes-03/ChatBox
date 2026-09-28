from __future__ import annotations

from ingestion.validate_corpus import RetrievalSource, ValidationCase

RETRIEVAL_FIXTURES: list[RetrievalSource] = [
    RetrievalSource(
        title="Parking",
        url="https://www.pnw.edu/parking/",
        content=(
            "Parking permits are required for students parking on the Hammond campus. "
            "Students may purchase permits online and must display them at all times. "
            "Westville parking rules are separate and should be reviewed on the campus-specific page."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Programs",
        url="https://www.pnw.edu/programs/",
        content=(
            "PNW offers undergraduate and graduate programs in business, engineering technology, "
            "education, and health sciences. Students should check program requirements before applying."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Registration",
        url="https://www.pnw.edu/registrar/registration/",
        content=(
            "Registration opens for continuing students according to class standing. "
            "Students must clear holds before the registration window closes."
        ),
        campus_scope="both",
        academic_term="fall",
    ),
    RetrievalSource(
        title="Plans of Study",
        url="https://catalog.pnw.edu/academic-plans/",
        content=(
            "Plans of study outline required courses, recommended sequencing, and graduation requirements. "
            "Students should review program-specific plans before scheduling classes."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Prerequisites",
        url="https://catalog.pnw.edu/prerequisites/",
        content=(
            "Courses may require completion of prerequisite coursework or department approval. "
            "Students who do not meet prerequisites may be prevented from enrolling."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Deadlines",
        url="https://www.pnw.edu/academic-calendar/",
        content=(
            "Important deadlines include the last day to add classes, withdraw without penalty, and final "
            "grade submission. Students should check the current academic calendar before making changes."
        ),
        campus_scope="both",
        academic_term="fall",
    ),
    RetrievalSource(
        title="Academic Integrity",
        url="https://www.pnw.edu/academic-integrity/",
        content=(
            "Academic integrity policies cover cheating, plagiarism, unauthorized collaboration, and improper use of AI. "
            "Students are expected to complete work honestly and follow course expectations."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Accessibility",
        url="https://www.pnw.edu/accessibility/",
        content=(
            "Accessibility services coordinate academic accommodations, assistive technology, and disability-related support. "
            "Students may request services through the Office of Student Disability Services."
        ),
        campus_scope="both",
        academic_term="current",
    ),
    RetrievalSource(
        title="Student Services",
        url="https://www.pnw.edu/student-services/",
        content=(
            "Student services provides advising, enrollment support, financial aid referrals, and campus engagement. "
            "Students can contact the office for help navigating academic and campus resources."
        ),
        campus_scope="both",
        academic_term="current",
    ),
]

RETRIEVAL_CASES: list[ValidationCase] = [
    ValidationCase(
        name="parking",
        question="What parking rules apply on the Hammond campus?",
        expected="supported",
        campus_scope="hammond",
    ),
    ValidationCase(
        name="programs",
        question="What programs are available at PNW?",
        expected="supported",
    ),
    ValidationCase(
        name="registration",
        question="How do I register for classes before the deadline?",
        expected="supported",
        academic_term="fall",
    ),
    ValidationCase(
        name="plans_of_study",
        question="What is a plan of study and how do I use it?",
        expected="supported",
    ),
    ValidationCase(
        name="prerequisites",
        question="What are the course prerequisites for enrollment?",
        expected="supported",
    ),
    ValidationCase(
        name="deadlines",
        question="When are the academic deadlines for fall?",
        expected="supported",
        academic_term="fall",
    ),
    ValidationCase(
        name="academic_integrity",
        question="What is the academic integrity policy?",
        expected="supported",
    ),
    ValidationCase(
        name="accessibility",
        question="How do I get accessibility accommodations?",
        expected="supported",
    ),
    ValidationCase(
        name="student_services",
        question="What student services are available at PNW?",
        expected="supported",
    ),
]


def get_retrieval_fixture_dataset() -> tuple[list[RetrievalSource], list[ValidationCase]]:
    return list(RETRIEVAL_FIXTURES), list(RETRIEVAL_CASES)
