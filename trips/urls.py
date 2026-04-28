"""URL routes for the trips app, mounted at ``/api/`` by the project urlconf."""
from __future__ import annotations

from rest_framework.routers import DefaultRouter

from trips.views import TripViewSet

router = DefaultRouter()
router.register(r"trips", TripViewSet, basename="trip")

urlpatterns = router.urls
