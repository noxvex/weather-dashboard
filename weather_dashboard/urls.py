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
    path("historie/piny/<int:pk>/upravit/", notes_views.pin_edit, name="pin_edit"),
    path("historie/piny/<int:pk>/smazat/", notes_views.pin_delete, name="pin_delete"),
    path("historie/piny/<int:pk>/pripnout/", notes_views.pin_toggle, name="pin_toggle"),
    path("", RedirectView.as_view(url="/aktuality/"), name="home"),
]