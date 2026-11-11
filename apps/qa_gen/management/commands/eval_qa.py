from django.core.management.base import BaseCommand

from apps.qa_gen.services.evaluate import run_eval


class Command(BaseCommand):
    help = "Ask Search every sampled kept Q&A pair in each retrieval mode and report accuracy by question type."

    def add_arguments(self, parser):
        parser.add_argument("--per-type", type=int, default=None, help="Pairs per question type (default QA_EVAL_MAX_PER_TYPE).")

    def handle(self, *args, **options):
        run = run_eval(per_type=options["per_type"])
        for line in run.report_md.splitlines():
            if line.startswith("## Per question"):
                break
            self.stdout.write(line)
        self.stdout.write(f"Saved as EvalRun {run.id} and in evals/results/.")
