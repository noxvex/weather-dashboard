from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import CustomUser

MAX_ATTEMPTS = 5


def _send_lockout_email(user):
    send_mail(
        subject=f"[Weather Dashboard] Účet zablokován: {user.username}",
        message=(
            f"Účet '{user.username}' byl zablokován po {MAX_ATTEMPTS} neúspěšných pokusech o přihlášení.\n"
            f"Čas: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Odemkněte účet v Django administraci."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.ADMIN_EMAIL],
        fail_silently=True,
    )


def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    error = None

    if request.method == "POST":
        username = request.POST.get("username", "").strip().lower()
        password = request.POST.get("password", "")

        # Look up the user first so we can check lockout and count failures
        try:
            user_obj = CustomUser.objects.get(username=username)
        except CustomUser.DoesNotExist:
            user_obj = None

        if user_obj and user_obj.is_locked:
            error = "locked"
        else:
            user = authenticate(request, username=username, password=password)

            if user is not None:
                # Successful login — reset the failure counter
                user.failed_login_attempts = 0
                user.save(update_fields=["failed_login_attempts"])
                login(request, user)
                return redirect(request.GET.get("next") or settings.LOGIN_REDIRECT_URL)
            else:
                error = "invalid"
                if user_obj is not None:
                    user_obj.failed_login_attempts += 1
                    if user_obj.failed_login_attempts >= MAX_ATTEMPTS:
                        user_obj.is_locked = True
                        _send_lockout_email(user_obj)
                        error = "locked"
                    user_obj.save(update_fields=["failed_login_attempts", "is_locked"])

    return render(request, "accounts/login.html", {"error": error})


def logout_view(request):
    logout(request)
    return redirect("accounts:login")
