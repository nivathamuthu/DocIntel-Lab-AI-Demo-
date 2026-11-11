from django.db import models

from apps.ingest.models import Chunk


class QAGeneration(models.Model):
    """One generation call: a chunk (factual + unanswerable) or a linked chunk pair (multi_hop).

    Cached by chunk(s), prompt version, provider and model; a changed chunk text changes `text_hash`.
    """

    chunk = models.ForeignKey(Chunk, related_name="qa_generations", on_delete=models.CASCADE)
    pair_chunk = models.ForeignKey(Chunk, null=True, blank=True, related_name="qa_pair_generations",
                                   on_delete=models.CASCADE)
    shared = models.CharField(max_length=300, blank=True)
    prompt_version = models.CharField(max_length=10)
    text_hash = models.CharField(max_length=64)
    llm_provider = models.CharField(max_length=20)
    llm_model = models.CharField(max_length=100, blank=True)
    raw_output = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["chunk_id", "pair_chunk_id"]

    def __str__(self) -> str:
        pair = f"+{self.pair_chunk_id}" if self.pair_chunk_id else ""
        return f"qa gen {self.chunk_id}{pair} ({self.prompt_version}, {self.llm_provider})"


class QAPair(models.Model):
    class QType(models.TextChoices):
        FACTUAL = "factual", "Factual"
        MULTI_HOP = "multi_hop", "Multi-hop"
        UNANSWERABLE = "unanswerable", "Unanswerable"

    class Verdict(models.TextChoices):
        KEPT = "kept", "Kept"
        DROPPED_QUOTE = "dropped_quote", "Dropped: quote not found"
        DROPPED_GROUNDEDNESS = "dropped_grounded", "Dropped: low groundedness"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    generation = models.ForeignKey(QAGeneration, related_name="pairs", on_delete=models.CASCADE)
    chunks = models.ManyToManyField(Chunk, related_name="qa_pairs")
    qtype = models.CharField(max_length=20, choices=QType.choices)
    question = models.TextField()
    answer = models.TextField()
    supporting_quotes = models.JSONField(default=list)
    prompt_version = models.CharField(max_length=10)
    quote_problem = models.CharField(max_length=300, blank=True)
    groundedness = models.FloatField(null=True, blank=True)
    judge_reason = models.TextField(blank=True)
    judge_prompt_version = models.CharField(max_length=10, blank=True)
    verdict = models.CharField(max_length=20, choices=Verdict.choices)
    review = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    llm_provider = models.CharField(max_length=20)
    llm_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Q{self.id} {self.qtype} ({self.verdict})"

    @property
    def status(self) -> str:
        """One label for the table: dropped, or the review state of a kept pair."""
        if self.verdict != self.Verdict.KEPT:
            return "dropped"
        return self.review

    @property
    def status_label(self) -> str:
        if self.verdict != self.Verdict.KEPT:
            return self.get_verdict_display()
        return {"pending": "Kept, needs review", "approved": "Approved", "rejected": "Rejected"}[self.review]


class QARun(models.Model):
    """Progress of one background generation or evaluation, polled by the page."""

    class Kind(models.TextChoices):
        GENERATE = "generate", "Generate"
        EVALUATE = "evaluate", "Evaluate"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=300, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    forced = models.BooleanField(default=False)
    llm_provider = models.CharField(max_length=20)
    llm_model = models.CharField(max_length=100, blank=True)
    eval_run_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"qa {self.kind} {self.status} {self.done}/{self.total}"

    @property
    def active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def percent(self) -> int:
        if not self.total:
            return 0 if self.active else 100
        return min(100, round(100 * self.done / self.total))
