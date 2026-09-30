"""Tests for the domain entities and values."""

from typing import Any
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.enums import PageAsset, TransformKind
from bookreviver.domain.ids import PageId, PageVersionId, StorageKey
from bookreviver.domain.values import BookDetails, Point, Progress, Quad, Renditions, Transform
from tests.helpers.builders import make_page, make_page_version, make_project, new_account_id

# The left half of a spread 2200 px wide and 1561 px high
QUAD: Quad = Quad(
    top_left=Point(x=0, y=0),
    top_right=Point(x=1100, y=0),
    bottom_right=Point(x=1100, y=1561),
    bottom_left=Point(x=0, y=1561),
)


class TestPageAssetKey:
    """Tests for Page.asset_key()."""

    def test_key_changes_with_asset_version(self) -> None:
        """Verify regenerated assets get new keys, so cached URLs never serve stale images."""
        page = make_page(project_id=make_project(owner_id=new_account_id()).id, index=4)
        regenerated = evolve(page, assets=evolve(page.assets, version=1))
        first, second = page.asset_key(PageAsset.TILES), regenerated.asset_key(PageAsset.TILES)
        assert (first.endswith('/pages/4/v0/iiif'), second.endswith('/pages/4/v1/iiif')) == (True, True)


class TestTransform:
    """Tests for the arguments a Transform takes by its kind."""

    @pytest.mark.parametrize(
        'arguments',
        [
            {},
            {'kind': TransformKind.CROP, 'quad': QUAD},
            {'kind': TransformKind.PERSPECTIVE, 'quad': QUAD},
            {'kind': TransformKind.ROTATE, 'angle': 0.8},
            {'kind': TransformKind.MESH, 'mesh_key': StorageKey('projects/book/assets/pages/1/mesh.json')},
        ],
        ids=['identity', 'crop', 'perspective', 'rotate', 'mesh'],
    )
    def test_kind_with_its_argument_is_accepted(self, arguments: dict[str, Any]) -> None:
        """Verify every kind is built from exactly its own argument, the identity from none.

        :param arguments: Keyword arguments of the transform.
        :type arguments: dict[str, Any]
        """
        assert Transform(**arguments).kind == arguments.get('kind', TransformKind.IDENTITY)

    @pytest.mark.parametrize(
        'arguments',
        [
            {'kind': TransformKind.CROP},
            {'kind': TransformKind.ROTATE, 'quad': QUAD},
            {'kind': TransformKind.IDENTITY, 'angle': 1.0},
            {'kind': TransformKind.CROP, 'quad': QUAD, 'angle': 1.0},
        ],
        ids=['missing-argument', 'argument-of-another-kind', 'identity-with-argument', 'extra-argument'],
    )
    def test_kind_without_exactly_its_argument_is_rejected(self, arguments: dict[str, Any]) -> None:
        """Reject a transform missing its argument or carrying one of another kind, which no step could apply.

        :param arguments: Keyword arguments of the transform.
        :type arguments: dict[str, Any]
        """
        with pytest.raises(ValueError, match='transform takes'):
            Transform(**arguments)


class TestPageVersion:
    """Tests for PageVersion invariants."""

    def test_renditions_past_their_first_version_are_rejected(self) -> None:
        """Verify a version's files are written once into its own directory, so they never get a second version."""
        version = make_page_version(page_id=PageId(uuid4()))
        with pytest.raises(ValueError, match='written once'):
            evolve(version, renditions=Renditions(version=Renditions.FIRST_VERSION + 1))

    def test_step_without_an_image_has_no_renditions(self) -> None:
        """Verify a version of a step without an image, such as recognition, carries no renditions."""
        version = make_page_version(page_id=PageId(uuid4()))
        assert evolve(version, renditions=None).renditions is None

    @pytest.mark.parametrize('version_id', ['8c21f0d7e4a6', '8C21F0D7E4A6B913', '../../../../etc/x'])
    def test_identifier_that_is_no_short_hash_is_rejected(self, version_id: str) -> None:
        """Reject an identifier that is not 16 lower-case hexadecimal digits, since it names a storage directory.

        :param version_id: Identifier that is no hash cut to 16 hexadecimal digits.
        :type version_id: str
        """
        with pytest.raises(ValueError, match='page version id'):
            evolve(make_page_version(page_id=PageId(uuid4())), id=PageVersionId(version_id))


class TestBookDetails:
    """Tests for BookDetails invariants."""

    def test_empty_title_is_rejected(self) -> None:
        """Verify a book cannot exist without a title."""
        with pytest.raises(ValueError, match='title'):
            BookDetails(title='')


class TestProgress:
    """Tests for Progress.fraction."""

    @pytest.mark.parametrize(('done', 'total', 'fraction'), [(0, 0, 0.0), (1, 4, 0.25), (4, 4, 1.0)])
    def test_fraction(self, done: int, total: int, fraction: float) -> None:
        """Verify the fraction is done over total, and zero while the total is unknown.

        :param done: Steps completed.
        :type done: int
        :param total: Steps in all, zero while unknown.
        :type total: int
        :param fraction: Fraction the progress must report.
        :type fraction: float
        """
        assert Progress(done=done, total=total).fraction == fraction
