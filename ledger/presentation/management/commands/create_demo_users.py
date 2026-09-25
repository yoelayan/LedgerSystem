"""Create the demo accounts (alice, bob, carol) so the four-eyes flow can be tried out.

Idempotent: existing users keep their password. Does nothing in production, so it can
run unconditionally at container start.
"""

import os
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEFAULT_PASSWORD = "demo1234"


class Command(BaseCommand):
    help = "Create the demo users listed in settings.DEMO_USERS."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEMO_USERS:
            self.stdout.write("demo users are disabled in this environment")
            return
        password = os.environ.get("DEMO_USERS_PASSWORD", DEFAULT_PASSWORD)
        user_model = get_user_model()
        for username in settings.DEMO_USERS:
            if user_model.objects.filter(username=username).exists():
                self.stdout.write(f"user {username} already exists")
                continue
            user_model.objects.create_user(username=username, password=password)
            self.stdout.write(f"created user {username}")
