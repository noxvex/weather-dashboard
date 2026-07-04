from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("prihlaseni/", views.login_view, name="login"),
    path("odhlaseni/", views.logout_view, name="logout"),
]
