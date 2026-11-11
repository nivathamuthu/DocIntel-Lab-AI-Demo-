from django.urls import path

from apps.qa_gen import views

app_name = "qa"

urlpatterns = [
    path("", views.index, name="index"),
    path("generate/", views.generate, name="generate"),
    path("evaluate/", views.evaluate, name="evaluate"),
    path("run/<int:run_id>/progress/", views.progress, name="progress"),
    path("pair/<int:pair_id>/", views.panel, name="panel"),
    path("pair/<int:pair_id>/review/", views.review, name="review"),
    path("export.<str:fmt>", views.export, name="export"),
    path("bulk-approve/", views.bulk_approve, name="bulk_approve"),
]
