"""Labels and validation for Around the League article categories."""
from __future__ import annotations

# GM-facing (and shared) categories — order matches commissioner UI.
NEWS_CATEGORY_CHOICES_GM: list[tuple[str, str]] = [
    ("transactions", "Transactions"),
    ("general_messages", "General Messages"),
    ("contract_news", "Contract News"),
    ("awards", "Awards"),
    ("injury_news", "Injury News"),
]

# Admin compose adds a sixth option; selecting it notifies every active GM in the league.
NEWS_CATEGORY_ADMIN_SUBMISSION = "admin_submission"

NEWS_CATEGORY_CHOICES_ADMIN: list[tuple[str, str]] = [
    *NEWS_CATEGORY_CHOICES_GM,
    (NEWS_CATEGORY_ADMIN_SUBMISSION, "Admin submission"),
]

_CATEGORY_LABELS: dict[str, str] = dict(NEWS_CATEGORY_CHOICES_ADMIN)


def news_category_label(slug: str | None) -> str:
    if not slug:
        return _CATEGORY_LABELS["general_messages"]
    return _CATEGORY_LABELS.get(slug, slug.replace("_", " ").title())


def normalize_news_category(raw: str | None, *, allow_admin: bool) -> str | None:
    """Return canonical slug or None if invalid."""
    s = (raw or "").strip()
    allowed = {c[0] for c in NEWS_CATEGORY_CHOICES_ADMIN} if allow_admin else {c[0] for c in NEWS_CATEGORY_CHOICES_GM}
    if s in allowed:
        return s
    return None
