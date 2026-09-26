"""IIIF tile pyramids and page images as immutable files."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

router = APIRouter(prefix='/iiif', tags=['iiif'], route_class=DishkaRoute)
