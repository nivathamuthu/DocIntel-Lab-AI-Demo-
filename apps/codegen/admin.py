from django.contrib import admin

from apps.codegen.models import CodegenAttempt, CodegenRun


class AttemptInline(admin.TabularInline):
    model = CodegenAttempt
    extra = 0
    fields = ("number", "status", "passed_count", "failed_count", "duration_ms")
    readonly_fields = fields


@admin.register(CodegenRun)
class CodegenRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "function_name", "attempts_used", "conflict_surfaced", "llm_provider", "created_at")
    list_filter = ("status", "llm_provider")
    inlines = [AttemptInline]
