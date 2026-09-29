from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("imaps/", include("ingestion.urls")),
    path("", include("frontend.urls")),
]
