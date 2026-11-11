from django.db import models


class CodegenRun(models.Model):
    """Selected rules -> one function + a pytest file, through the self-repair loop."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PASSED = "passed", "All tests passed"
        NEEDS_REVIEW = "needs_review", "Needs review"
        FAILED = "failed", "Failed"

    rule_keys = models.JSONField(default=list)
    added_keys = models.JSONField(default=list, blank=True)
    rules = models.JSONField(default=list)
    conflicts = models.JSONField(default=list, blank=True)
    unreviewed = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    function_name = models.CharField(max_length=60, blank=True)
    parameters = models.JSONField(default=list, blank=True)
    cases = models.JSONField(default=list, blank=True)
    case_problems = models.JSONField(default=list, blank=True)
    final_code = models.TextField(blank=True)
    final_tests = models.TextField(blank=True)
    attempts_used = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    conflict_surfaced = models.BooleanField(null=True)
    mutation = models.JSONField(default=dict, blank=True)
    llm_provider = models.CharField(max_length=20)
    llm_model = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=10, default="ui")
    review_reason = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"codegen {self.id} {','.join(self.rule_keys)} ({self.status})"

    @property
    def active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)


class CodegenAttempt(models.Model):
    """One run of the generated tests against one version of the code."""

    class Status(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"
        TIMEOUT = "timeout", "Timed out"

    run = models.ForeignKey(CodegenRun, related_name="attempts", on_delete=models.CASCADE)
    number = models.PositiveSmallIntegerField()
    code = models.TextField()
    tests = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices)
    pytest_output = models.TextField(blank=True)
    blocked = models.JSONField(default=list, blank=True)
    per_test = models.JSONField(default=dict, blank=True)
    verdicts = models.JSONField(default=list, blank=True)
    passed_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run_id", "number"]

    def __str__(self) -> str:
        return f"codegen {self.run_id} attempt {self.number} ({self.status})"
