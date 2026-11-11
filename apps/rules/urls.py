from django.urls import path

from apps.rules import views

app_name = "rules"

urlpatterns = [
    path("", views.index, name="index"),
    path("extract/", views.extract, name="extract"),
    path("export.<str:fmt>", views.export, name="export"),
    path("bulk-approve/", views.bulk_approve, name="bulk_approve"),
    path("<str:key>/", views.detail, name="detail"),
    path("<str:key>/review/", views.review, name="review"),
]
