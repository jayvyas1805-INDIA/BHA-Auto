# detector.py
import re

# Signals specific enough that they don't appear outside the BHA chapter.
# "OD"/"ID" are deliberately excluded from plain substring checks below --
# those two-letter tokens matched inside unrelated words like "provided" or
# "considered" and caused the detector to fire on almost every page.
STRONG_SIGNALS = [
    r"\bbha\s*no\b",
    r"\bstring\s*component\b",
]

SOFT_SIGNALS = [
    r"\bwellbore\b",
    r"\bod\s*in\b",
    r"\bid\s*in\b",
    r"\blength\s*m\b",
    r"\bacc\s*length\b",
    r"\brun\s*type\b",
    r"\brun\s*name\b",
    r"\bbha\s*name\b",
    r"\bbha\s*kind\b",
]

BUFFER = 1


def score_page(page):

    text = page.get("text", "").lower()

    strong = sum(1 for pat in STRONG_SIGNALS if re.search(pat, text))
    soft = sum(1 for pat in SOFT_SIGNALS if re.search(pat, text))

    score = strong * 3 + soft  # strong signals dominate; soft ones just add confidence

    tables = page.get("tables", [])
    if tables:
        score += 2

    return score




def detect_section(pages):

    selected=[]

    inside=False

    buffer=None


    for page in pages:

        score = score_page(
            page
        )


        if score >= 8:

            inside=True

            buffer=None

            selected.append(
                page
            )

            continue


        if inside:

            selected.append(
                page
            )

            # Only a still-strong page (not just background "wellbore"/"od in"
            # noise from unrelated chapters) should keep the section open.
            if score >= 6:

                buffer=None


            else:

                if buffer is None:

                    buffer=BUFFER

                else:

                    buffer-=1


                if buffer < 0:
                    selected = selected[:-(BUFFER + 1)]  # drop the trailing non-matching pages
                    break


    return selected