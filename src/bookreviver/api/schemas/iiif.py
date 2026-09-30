"""What the API knows of the IIIF Image API documents it serves, which is only how it names and types one.

The image information document, ``info.json``, is written by the tiler next to the tiles of a pyramid, with the path
of the IIIF route as its ``id``, and the route serves it exactly as it was written. The API therefore models neither
the document nor its properties.
"""

# Name of the image information document at the root of a tile pyramid
IIIF_INFO_FILE: str = 'info.json'
# Media type IIIF Image API 3 recommends for the document
IIIF_INFO_MEDIA_TYPE: str = 'application/ld+json;profile="http://iiif.io/api/image/3/context.json"'
