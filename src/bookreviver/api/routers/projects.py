"""Project list, creation, book description and deletion."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(prefix='/projects', tags=['projects'], route_class=DishkaRoute)
