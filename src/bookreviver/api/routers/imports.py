"""Upload of a book source and start of its import."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(prefix='/projects', tags=['imports'], route_class=DishkaRoute)
