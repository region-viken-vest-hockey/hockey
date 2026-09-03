from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "ownership-and-handover.md"


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_ownership_handover_guide_exists_and_covers_inventory() -> None:
    text = _guide_text()

    required_dependencies = [
        "GitHub repository",
        "GitHub Pages",
        "GitHub teams, roles, and protected environments",
        "Microsoft Forms registrations",
        "SharePoint site, Lists, and Excel workbooks",
        "Power Automate flows and connections",
        "Spond group administration",
        "WordPress administrator/editor accounts",
        "Calendar-source credentials and service accounts",
        "Domains, DNS, analytics, and notification addresses",
    ]

    for dependency in required_dependencies:
        assert dependency in text


def test_ownership_handover_guide_keeps_handover_critical_sections() -> None:
    text = _guide_text()

    required_sections_and_phrases = [
        "## Operator role matrix",
        "least privilege",
        "## Managed secrets and rotation",
        "managed secret stores",
        "## Handover procedure",
        "## Annual access review checklist",
        "## Emergency recovery when the primary maintainer is unavailable",
        "## Second-person end-to-end dry run",
        "without borrowing the original maintainer's login",
        "## Private operations record template",
    ]

    for phrase in required_sections_and_phrases:
        assert phrase in text


def test_operator_facing_docs_link_to_ownership_handover_guide() -> None:
    docs = [
        ROOT / "README.md",
        ROOT / "docs" / "rvv-miniputt-deployment-architecture.md",
        ROOT / "docs" / "ai-operator-product-direction.md",
    ]

    for path in docs:
        assert "ownership-and-handover.md" in path.read_text(encoding="utf-8")
