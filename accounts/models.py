from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    ROLE_ADMIN = "admin"
    ROLE_LEADER = "leader"
    ROLE_WORKER = "worker"
    ROLE_CHOICES = [
        (ROLE_ADMIN, "Admin"),
        (ROLE_LEADER, "Leader"),
        (ROLE_WORKER, "Worker"),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_WORKER)
    is_locked = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "uživatel"
        verbose_name_plural = "uživatelé"

    def __str__(self):
        return self.username
