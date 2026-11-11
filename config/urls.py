from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import home, results

urlpatterns = [
    path("", home, name="home"),
    path("results/", results, name="results"),
    path("admin/", admin.site.urls),
    path("ingest/", include("apps.ingest.urls")),
    path("prompts/", include("apps.prompts.urls")),
    path("rag/", include("apps.rag.urls")),
    path("kg/", include("apps.kg.urls")),
    path("rules/", include("apps.rules.urls")),
    path("qa/", include("apps.qa_gen.urls")),
    path("codegen/", include("apps.codegen.urls")),
    path("agents/", include("apps.agents.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
