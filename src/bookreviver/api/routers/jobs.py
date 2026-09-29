"""Background job state, cancellation and project event streams."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(prefix='/jobs', tags=['jobs'], route_class=DishkaRoute)
