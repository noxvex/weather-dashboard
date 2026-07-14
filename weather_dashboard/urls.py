from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from notes import views as notes_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("aktuality/", include("notes.urls")),
    path("bod/", notes_views.point_detail, name="bod"),
    path("historie/", notes_views.historie, name="historie"),
    path("historie/piny/nova/", notes_views.pin_create, name="pin_create"),
    path("", RedirectView.as_view(url="/aktuality/"), name="home"),
]