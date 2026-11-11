from django.shortcuts import render

from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun

MODULES = [
    {"n": "1", "name": "Ingest", "url": "ingest:index", "planned": False,
     "line": "Upload a PDF, DOCX or PPTX and follow the seven pipeline stages, with page, section and identifiers on each chunk."},
    {"n": "2", "name": "Prompts", "url": "prompts:compare", "planned": False,
     "line": "Compare prompt v1 and v2 on the same samples, with a score for each."},
    {"n": "3", "name": "Search (RAG)", "url": "rag:query", "planned": False,
     "line": "Ask with vector, BM25, hybrid or hybrid plus rerank, and read the citation."},
    {"n": "4", "name": "Graph", "url": "kg:index", "planned": False,
     "line": "Browse the knowledge graph, then ask the same question with GraphRAG beside plain hybrid."},
    {"n": "5", "name": "Rules", "url": "rules:index", "planned": False,
     "line": "Review extracted rules, merged duplicates and conflicts, with the source quote highlighted."},
    {"n": "6", "name": "Q&A", "url": "qa:index", "planned": False,
     "line": "Generate grounded question and answer pairs from the chunks, review them, and use them to test Search."},
    {"n": "7", "name": "Code", "url": "codegen:index", "planned": False,
     "line": "Turn approved rules into a Python function and pytest tests, with a self-repair loop and a sandboxed test run."},
    {"n": "8", "name": "Agent", "url": "agents:index", "planned": False,
     "line": "Ask a compliance question and follow the LangGraph trace through retrieval, rules, citation check and approval."},
]

EVAL_KINDS = [
    ("prompts", "2 · Prompts"),
    ("rag", "3 · Retrieval"),
    ("kg", "4 · GraphRAG"),
    ("rules", "5 · Rules"),
    ("qa", "6 · Q&A against Search"),
    ("codegen", "7 · Rules to code"),
    ("agent", "8 · Agent"),
]


def home(request):
    return render(request, "home.html", {
        "modules": MODULES,
        "llm": describe_backend(),
        "embeddings": describe_embeddings(),
    })


def results(request):
    sections = []
    for kind, title in EVAL_KINDS:
        run = EvalRun.objects.filter(kind=kind).first()
        sections.append({"kind": kind, "title": title, "run": run})
    return render(request, "results.html", {"sections": sections})
