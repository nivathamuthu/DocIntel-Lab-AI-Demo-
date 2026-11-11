from django.contrib import admin

from apps.qa_gen.models import QAGeneration, QAPair, QARun


@admin.register(QAPair)
class QAPairAdmin(admin.ModelAdmin):
    list_display = ("id", "qtype", "verdict", "review", "groundedness", "question", "llm_provider")
    list_filter = ("qtype", "verdict", "review", "llm_provider")
    search_fields = ("question", "answer")


admin.site.register(QAGeneration)
admin.site.register(QARun)
