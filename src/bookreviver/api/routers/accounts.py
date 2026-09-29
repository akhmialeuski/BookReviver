"""Sign-in, registration, account settings and provider credentials."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(tags=['accounts'], route_class=DishkaRoute)
