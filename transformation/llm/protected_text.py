"""Protect email addresses during translation."""
import re
import uuid
from collections import Counter

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


def protect_emails(text):
    """Replace each email occurrence with a unique placeholder."""
    prefix = f"ZXKEEP{uuid.uuid4().hex[:8].upper()}X"
    while prefix in text:
        prefix = f"ZXKEEP{uuid.uuid4().hex[:8].upper()}X"

    replacements = {}

    def replace(match):
        token = f"{prefix}{len(replacements)}XZ"
        replacements[token] = match.group(0)
        return token

    protected = EMAIL_PATTERN.sub(replace, text)
    return protected, replacements


def restore_emails(translated, replacements):
    """Restore emails only if every placeholder survived exactly once."""
    for token in replacements:
        if translated.count(token) != 1:
            raise ValueError(
                "Translation changed, omitted, or duplicated an email "
                "placeholder. Review required."
            )

    restored = translated
    for token, original in replacements.items():
        restored = restored.replace(token, original)

    expected = Counter(replacements.values())
    actual = Counter(EMAIL_PATTERN.findall(restored))

    if actual != expected:
        raise ValueError(
            "Email addresses in the translation do not match the source. "
            "Review required."
        )

    return restored
def normalize_protected_values(values=()):
    """Canonical, case-sensitive values for matching and cache identity."""
    if isinstance(values, str):
        raise TypeError("Pass values as a list or tuple, not one string.")

    values = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Protected values must be non-empty strings.")

    return tuple(sorted(set(values), key=lambda value: (-len(value), value)))


def protect_values(text, values=()):
    """Protect emails and explicitly supplied literal values."""
    literals = normalize_protected_values(values)

    # Conservative structured literals; this is preservation, not PII detection.
    # Names outside this narrow hyphenated form still require explicit values.
    alternatives = [r'\{v\d+\}', EMAIL_PATTERN.pattern,
                    r'\b\d{4}-\d{2}-\d{2}[T ](?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:Z|[+-]\d{2}:\d{2}|\s+[A-Z]{2,5})?\b',
                    r'\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s+[A-Z]{2,5})?\b',
                    r'\b[A-Z]{3}\s*\d+(?:,\d+)*(?:\.\d+)?\b',
                    r'[$€£¥₩]\s*\d+(?:,\d+)*(?:\.\d+)?',
                    r'\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-[ \t]+\d+[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\b',
                    r'\b(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b',
                    r'\b[A-Za-z]+\d[\w.-]*\b',
                    r'\b(?=[IVXLCDM]{2,8}\b)M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})(?<=[IVXLCDM])\b',
                    r'\b[A-Z][a-z]+ [A-Z][a-z]+-[a-z]+\b',
                    r'\b[A-Z]{2,}(?:-[A-Za-z0-9]+)+\b',
                    r'\b\d{4}-\d{2}-\d{2}\b',
                    r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
                    r'\bsk_[A-Za-z0-9_.-]+\b',
                    r'(?<!\w)\d+(?:,\d+)*(?:\.\d+)?%?(?!\w)']
    if literals:
        alternatives.append(
            r"(?<!\w)(?:"
            + "|".join(re.escape(value) for value in literals)
            + r")(?!\w)"
        )

    # Latin identifiers/numeric literals can be followed by Korean particles.
    # Unicode \b/\w would treat that attachment as part of the same word and
    # split or lose an otherwise unchanged date or code during validation.
    pattern = re.compile("|".join(alternatives), re.ASCII)

    prefix = f"ZXKEEP{uuid.uuid4().hex[:8].upper()}X"
    while prefix in text:
        prefix = f"ZXKEEP{uuid.uuid4().hex[:8].upper()}X"

    replacements = {}

    def replace(match):
        token = f"{prefix}{len(replacements)}XZ"
        replacements[token] = match.group(0)
        return token

    return pattern.sub(replace, text), replacements


class PlaceholderIntegrityError(ValueError):
    """Exact placeholder occurrence counts failed validation."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__(
            "Protected placeholder check failed: "
            + "; ".join(
                f"placeholder {issue['index']}: expected 1, found {issue['actual']}"
                for issue in issues
            )
        )


def restore_values(translated, replacements):
    """Require each placeholder exactly once, then restore its original value."""
    issues = [
        {"index": index, "token": token, "actual": translated.count(token)}
        for index, token in enumerate(replacements, start=1)
        if translated.count(token) != 1
    ]
    if issues:
        raise PlaceholderIntegrityError(issues)

    restored = translated
    for token, original in replacements.items():
        restored = restored.replace(token, original)

    # Keep the existing protection against invented or altered emails.
    expected_emails = Counter(
        email
        for original in replacements.values()
        for email in EMAIL_PATTERN.findall(original)
    )
    actual_emails = Counter(EMAIL_PATTERN.findall(restored))

    if actual_emails != expected_emails:
        raise ValueError("Email addresses do not match the protected source.")

    return restored
