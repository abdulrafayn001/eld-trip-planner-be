"""URL routes for the trips app, mounted at ``/api/`` by the project urlconf."""
from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from trips.views import LoginView, RegisterView, TripViewSet

router = DefaultRouter()
router.register(r"trips", TripViewSet, basename="trip")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    *router.urls,
]
