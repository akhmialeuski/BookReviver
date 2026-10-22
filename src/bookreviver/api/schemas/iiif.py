"""What the API knows of the IIIF Image API documents it serves, which is only how it names and types one.

The image information document, ``info.json``, is written by the tiler next to the tiles of a pyramid, and the route
serves it with its ``id`` set to the address it was asked at, the one property the API touches. The API therefore
models neither the document nor its other properties.
"""

# Name of the image information document at the root of a tile pyramid
IIIF_INFO_FILE: str = 'info.json'
# Media type IIIF Image API 3 recommends for the document
IIIF_INFO_MEDIA_TYPE: str = 'application/ld+json;profile="http://iiif.io/api/image/3/context.json"'
