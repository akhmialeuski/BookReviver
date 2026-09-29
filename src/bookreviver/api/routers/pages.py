"""Page manifest and page facts for the viewer."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(prefix='/projects', tags=['pages'], route_class=DishkaRoute)
