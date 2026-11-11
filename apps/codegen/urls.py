from django.urls import path

from apps.codegen import views

app_name = "codegen"

urlpatterns = [
    path("", views.index, name="index"),
    path("start/", views.start, name="start"),
    path("run/<int:run_id>/", views.run_detail, name="run"),
    path("run/<int:run_id>/body/", views.run_body, name="run_body"),
    path("run/<int:run_id>/download/<str:name>", views.download, name="download"),
]
