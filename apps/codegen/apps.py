from django.apps import AppConfig


class CodegenConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.codegen"
    label = "codegen"
    verbose_name = "Rules to code"

    def ready(self) -> None:
        from apps.codegen import mocking  # noqa: F401  registers the offline code and test writers
