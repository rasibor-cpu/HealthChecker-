"""HC311-F8D-QR1 HealthChecker recovery-question databank.

System-owned, versioned catalog of selectable recovery questions.

Users select questions from this catalog. Users cannot create or modify
system questions during enrollment.

This module contains NO user answers and NO production secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


QUESTION_BANK_VERSION: Final[int] = 1
REQUIRED_SELECTION_COUNT: Final[int] = 10


@dataclass(frozen=True)
class RecoveryQuestion:
    question_id: str
    category: str
    prompt: str


QUESTION_BANK: Final[tuple[RecoveryQuestion, ...]] = (

    # --------------------------------------------------------
    # CHILDHOOD & HOME
    # --------------------------------------------------------

    RecoveryQuestion(
        "CH01", "Childhood & Home",
        "What street did you live on during an especially memorable year of your childhood?"
    ),
    RecoveryQuestion(
        "CH02", "Childhood & Home",
        "What was the name of the neighborhood where you spent most of your childhood?"
    ),
    RecoveryQuestion(
        "CH03", "Childhood & Home",
        "What was the name of a memorable childhood neighbor?"
    ),
    RecoveryQuestion(
        "CH04", "Childhood & Home",
        "What landmark was closest to a childhood home you remember especially well?"
    ),
    RecoveryQuestion(
        "CH05", "Childhood & Home",
        "What was the name your family used for a childhood home or family property?"
    ),
    RecoveryQuestion(
        "CH06", "Childhood & Home",
        "What street did you live on while you were in Grade 11 or its equivalent?"
    ),

    # --------------------------------------------------------
    # SCHOOL & EDUCATION
    # --------------------------------------------------------

    RecoveryQuestion(
        "ED01", "School & Education",
        "What was the name of the first school you attended?"
    ),
    RecoveryQuestion(
        "ED02", "School & Education",
        "What was the name of your primary or elementary school?"
    ),
    RecoveryQuestion(
        "ED03", "School & Education",
        "What was the surname of a teacher you remember especially well?"
    ),
    RecoveryQuestion(
        "ED04", "School & Education",
        "What was the first name of a teacher you remember especially well?"
    ),
    RecoveryQuestion(
        "ED05", "School & Education",
        "What was the name of a school or institution where you completed an important qualification?"
    ),
    RecoveryQuestion(
        "ED06", "School & Education",
        "What neighborhood or town was an important school you attended located in?"
    ),

    # --------------------------------------------------------
    # FRIENDS & SOCIAL LIFE
    # --------------------------------------------------------

    RecoveryQuestion(
        "FR01", "Friends & Social Life",
        "What was the first name of a childhood friend you remember especially well?"
    ),
    RecoveryQuestion(
        "FR02", "Friends & Social Life",
        "What was the first name of your closest friend in secondary or high school?"
    ),
    RecoveryQuestion(
        "FR03", "Friends & Social Life",
        "What was the surname of a close school friend you remember especially well?"
    ),
    RecoveryQuestion(
        "FR04", "Friends & Social Life",
        "Where did you regularly meet friends during your school years?"
    ),
    RecoveryQuestion(
        "FR05", "Friends & Social Life",
        "What was the name of a childhood sports team, club, or group you belonged to?"
    ),
    RecoveryQuestion(
        "FR06", "Friends & Social Life",
        "What was the name of a social club, association, or group that was important to you when you were younger?"
    ),

    # --------------------------------------------------------
    # FAMILY HISTORY
    # --------------------------------------------------------

    RecoveryQuestion(
        "FA01", "Family History",
        "In what town or neighborhood did your parents first meet?"
    ),
    RecoveryQuestion(
        "FA02", "Family History",
        "What place did your family visit repeatedly when you were young?"
    ),
    RecoveryQuestion(
        "FA03", "Family History",
        "What was the name of a town or village strongly associated with your family?"
    ),
    RecoveryQuestion(
        "FA04", "Family History",
        "What nickname did your family use for a relative you remember especially well?"
    ),
    RecoveryQuestion(
        "FA05", "Family History",
        "What was the name of a family friend you remember from childhood?"
    ),
    RecoveryQuestion(
        "FA06", "Family History",
        "What town or neighborhood did a grandparent you remember well live in?"
    ),

    # --------------------------------------------------------
    # WORK & CAREER
    # --------------------------------------------------------

    RecoveryQuestion(
        "WK01", "Work & Career",
        "What was the name of your first employer?"
    ),
    RecoveryQuestion(
        "WK02", "Work & Career",
        "In what city did you receive your first full-time salary?"
    ),
    RecoveryQuestion(
        "WK03", "Work & Career",
        "What was the surname of your first manager or supervisor?"
    ),
    RecoveryQuestion(
        "WK04", "Work & Career",
        "What street or neighborhood was your first workplace located in?"
    ),
    RecoveryQuestion(
        "WK05", "Work & Career",
        "What was your first formal job title?"
    ),
    RecoveryQuestion(
        "WK06", "Work & Career",
        "What organization gave you an especially memorable early-career opportunity?"
    ),

    # --------------------------------------------------------
    # TRAVEL & PLACES
    # --------------------------------------------------------

    RecoveryQuestion(
        "TR01", "Travel & Places",
        "What was the destination of your first trip outside your home country?"
    ),
    RecoveryQuestion(
        "TR02", "Travel & Places",
        "In what city did you spend your honeymoon, if applicable?"
    ),
    RecoveryQuestion(
        "TR03", "Travel & Places",
        "What was the destination of a childhood trip you remember especially well?"
    ),
    RecoveryQuestion(
        "TR04", "Travel & Places",
        "What was the first foreign city you remember visiting?"
    ),
    RecoveryQuestion(
        "TR05", "Travel & Places",
        "What town or city was the destination of an especially memorable road trip?"
    ),
    RecoveryQuestion(
        "TR06", "Travel & Places",
        "What place did you repeatedly visit for holidays or vacations?"
    ),

    # --------------------------------------------------------
    # VEHICLES & TRANSPORT
    # --------------------------------------------------------

    RecoveryQuestion(
        "VE01", "Vehicles & Transport",
        "What was the colour of your first car?"
    ),
    RecoveryQuestion(
        "VE02", "Vehicles & Transport",
        "What was the make or model of the first car you regularly drove?"
    ),
    RecoveryQuestion(
        "VE03", "Vehicles & Transport",
        "What was the make of a vehicle your family used frequently when you were young?"
    ),
    RecoveryQuestion(
        "VE04", "Vehicles & Transport",
        "What nickname, if any, did you or your family give a memorable vehicle?"
    ),
    RecoveryQuestion(
        "VE05", "Vehicles & Transport",
        "What was the first vehicle make you personally owned?"
    ),
    RecoveryQuestion(
        "VE06", "Vehicles & Transport",
        "Where did you usually travel when you first learned to drive?"
    ),

    # --------------------------------------------------------
    # PERSONAL MILESTONES
    # --------------------------------------------------------

    RecoveryQuestion(
        "MI01", "Personal Milestones",
        "In what city did you celebrate an especially important personal milestone?"
    ),
    RecoveryQuestion(
        "MI02", "Personal Milestones",
        "What venue hosted an important celebration you remember especially well?"
    ),
    RecoveryQuestion(
        "MI03", "Personal Milestones",
        "What was the first major item you remember buying with money you earned yourself?"
    ),
    RecoveryQuestion(
        "MI04", "Personal Milestones",
        "What city were you living in when you achieved an important qualification?"
    ),
    RecoveryQuestion(
        "MI05", "Personal Milestones",
        "What place do you associate with an especially important personal achievement?"
    ),
    RecoveryQuestion(
        "MI06", "Personal Milestones",
        "What organization or institution do you associate with an important turning point in your life?"
    ),

    # --------------------------------------------------------
    # COMMUNITY & CULTURE
    # --------------------------------------------------------

    RecoveryQuestion(
        "CU01", "Community & Culture",
        "What community organization did you first participate in regularly?"
    ),
    RecoveryQuestion(
        "CU02", "Community & Culture",
        "What was the name of a local market, park, square, or gathering place you knew well growing up?"
    ),
    RecoveryQuestion(
        "CU03", "Community & Culture",
        "What was the name of a memorable local sports club or team from where you grew up?"
    ),
    RecoveryQuestion(
        "CU04", "Community & Culture",
        "What neighborhood gathering place do you remember especially well from your youth?"
    ),
    RecoveryQuestion(
        "CU05", "Community & Culture",
        "What annual event or celebration did your family regularly attend when you were young?"
    ),
    RecoveryQuestion(
        "CU06", "Community & Culture",
        "What local landmark strongly reminds you of where you grew up?"
    ),

    # --------------------------------------------------------
    # FINANCIAL & INDEPENDENT LIFE
    # --------------------------------------------------------

    RecoveryQuestion(
        "LI01", "Independent Life",
        "What was the name of the first bank where you personally held an account?"
    ),
    RecoveryQuestion(
        "LI02", "Independent Life",
        "What neighborhood was your first independently chosen home located in?"
    ),
    RecoveryQuestion(
        "LI03", "Independent Life",
        "What was the first major household item you bought for yourself?"
    ),
    RecoveryQuestion(
        "LI04", "Independent Life",
        "What city were you living in when you first became financially independent?"
    ),
    RecoveryQuestion(
        "LI05", "Independent Life",
        "What was the name of the first company from which you personally bought a major product or service?"
    ),
    RecoveryQuestion(
        "LI06", "Independent Life",
        "What location do you associate with your first major independent financial decision?"
    ),
)


def question_count() -> int:
    return len(QUESTION_BANK)


def categories() -> tuple[str, ...]:
    return tuple(dict.fromkeys(q.category for q in QUESTION_BANK))


def questions_for_category(category: str) -> tuple[RecoveryQuestion, ...]:
    return tuple(
        q for q in QUESTION_BANK
        if q.category == category
    )


def question_by_id(question_id: str) -> RecoveryQuestion:
    for question in QUESTION_BANK:
        if question.question_id == question_id:
            return question
    raise KeyError(question_id)
