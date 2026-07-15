from django.urls import path
from . import views

app_name = "notes"

urlpatterns = [
    path("", views.aktuality, name="aktuality"),
    path("nova/", views.note_create, name="create"),
    path("<int:pk>/upravit/", views.note_edit, name="edit"),
    path("<int:pk>/smazat/", views.note_delete, name="delete"),
    path("<int:pk>/pripnout/", views.note_pin, name="pin"),
    path("revize/", views.revision_tracker, name="revision_tracker"),
    path("revize/zkontrolovat/", views.revize_check_now, name="revize_check"),
]
