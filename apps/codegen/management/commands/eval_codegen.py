from django.core.management.base import BaseCommand

from apps.codegen.evaluate import run_eval


class Command(BaseCommand):
    help = "Generate code and tests for the rules in evals/datasets/codegen_cases.yaml and report the results."

    def handle(self, *args, **options):
        run = run_eval()
        self.stdout.write(run.report_md)
        self.stdout.write(f"Saved as EvalRun {run.id} and in evals/results/.")
