"""The recording landing page and the results page."""
import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_home_lists_all_eight_modules_as_built(client):
    page = client.get(reverse("home")).content.decode()
    assert "planned" not in page
    for name in ("Ingest", "Prompts", "RAG", "Graph", "Rules", "Agent"):
        assert name in page
    assert "Q&amp;A" in page or "Q&A" in page
    assert reverse("qa:index") in page
    assert reverse("codegen:index") in page
    assert reverse("ingest:index") in page
    assert reverse("agents:index") in page
    assert reverse("results") in page
    assert "Known limitations: demo build, no authentication" in page


def test_results_page_renders_with_no_evals(client):
    page = client.get(reverse("results")).content.decode()
    assert page.count("No eval yet.") == 7


def test_nav_highlights_the_current_page(client):
    page = client.get(reverse("codegen:index")).content.decode()
    assert '<a class="nav-link active" href="/codegen/" aria-current="page">' in page
    assert page.count('aria-current="page"') == 1
    assert "Demo build, no authentication" in page
