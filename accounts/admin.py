from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "role", "is_locked", "failed_login_attempts", "is_active", "date_joined")
    list_filter = ("role", "is_locked", "is_active")
    list_editable = ("is_locked",)

    fieldsets = UserAdmin.fieldsets + (
        ("Role & Lockout", {"fields": ("role", "is_locked", "failed_login_attempts")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Role", {"fields": ("role",)}),
    )
