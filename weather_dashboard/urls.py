from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("aktuality/", include("notes.urls")),
    # Temporary root redirect until Phase 3 builds the forecast home
    path("", RedirectView.as_view(url="/aktuality/"), name="home"),
]
