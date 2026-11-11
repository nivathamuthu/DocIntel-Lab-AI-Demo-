from django.core.management.base import BaseCommand

from apps.qa_gen.services.generate import run_generation
from core.llm import describe_backend


class Command(BaseCommand):
    help = "Generate Q&A pairs per chunk and per graph-linked chunk pair, check quotes and judge groundedness."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Regenerate cached chunks (clears their reviews).")
        parser.add_argument("--prompt-version", default=None, help="generate_qa prompt version (default QA_PROMPT_VERSION).")

    def handle(self, *args, **options):
        backend = describe_backend()
        self.stdout.write(f"LLM {backend['provider']} / {backend['model']}")
        stats = run_generation(force=options["force"], version=options["prompt_version"])
        totals = stats["totals"]
        self.stdout.write(
            f"{stats['chunks']} chunks, {stats['linked_pairs']} linked pairs: {stats['ran']} sent to the model, "
            f"{stats['cached']} cached, {stats['failed']} failed validation."
        )
        self.stdout.write(
            f"generated {totals['generated']} · kept {totals['kept']} · dropped (quote not found) "
            f"{totals['dropped_quote']} · dropped (low groundedness) {totals['dropped_grounded']} · "
            f"approved {totals['approved']}"
        )
        for failure in stats["failures"]:
            self.stdout.write(f"  failed: {failure}")
