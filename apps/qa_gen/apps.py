from django.apps import AppConfig


class QaGenConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.qa_gen"
    label = "qa_gen"
    verbose_name = "Q&A generation"

    def ready(self) -> None:
        from apps.qa_gen import mocking  # noqa: F401  registers the offline generator and judges
