"""IIIF tile pyramids and page images as immutable files."""

from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

# Where the router is mounted below the API prefix, which a pyramid's ``info.json`` names as its address
IIIF_PREFIX: str = '/iiif'

router = APIRouter(prefix=IIIF_PREFIX, tags=['iiif'], route_class=DishkaRoute)
