"""Tests for the layout of storage keys."""

from uuid import uuid4

import pytest

from bookreviver.domain.enums import PageAsset
from bookreviver.domain.ids import ProjectId, StorageKey
from bookreviver.domain.keys import ProjectKeys
from tests.helpers.builders import make_page

PROJECT_ID: ProjectId = ProjectId(uuid4())
OTHER_PROJECT_ID: ProjectId = ProjectId(uuid4())
PREFIX: StorageKey = ProjectKeys(PROJECT_ID).prefix
ASSET_PARAM: str = 'asset'


class TestPrefix:
    """Tests for ProjectKeys.prefix."""

    @pytest.mark.parametrize(ASSET_PARAM, list(PageAsset))
    def test_every_page_asset_lies_under_the_prefix(self, asset: PageAsset) -> None:
        """Verify deleting the prefix reaches every derived file of the project's pages."""
        page = make_page(project_id=PROJECT_ID, index=3)
        assert page.asset_key(asset).startswith(PREFIX)

    def test_prefix_does_not_cover_a_project_whose_id_extends_it(self) -> None:
        """Verify the prefix ends with the separator, so it never matches a longer sibling name."""
        assert not StorageKey(f'{PREFIX[:-1]}x/pages').startswith(PREFIX)


class TestOwning:
    """Tests for ProjectKeys.owning()."""

    @pytest.mark.parametrize(ASSET_PARAM, list(PageAsset))
    def test_page_asset_key_belongs_to_its_project(self, asset: PageAsset) -> None:
        """Verify the key of every page asset is recognised as the project's."""
        page = make_page(project_id=PROJECT_ID, index=0)
        assert ProjectKeys.owning(page.asset_key(asset)) == ProjectKeys(PROJECT_ID)

    @pytest.mark.parametrize(
        'key',
        [
            f'sources/{PROJECT_ID}/book.pdf',
            'projects/not-an-id/pages/0/v1/thumb.jpg',
            PREFIX[:-1],
            PREFIX,
            f'{PREFIX}../{OTHER_PROJECT_ID}/pages/0/v1/thumb.jpg',
            f'{PREFIX}./pages/0/v1/thumb.jpg',
            f'{PREFIX}/pages',
            '',
        ],
        ids=[
            'other-root',
            'bad-id',
            'project-itself',
            'trailing-slash',
            'climbs-out',
            'dot-segment',
            'empty-segment',
            'empty',
        ],
    )
    def test_malformed_key_belongs_to_no_project(self, key: str) -> None:
        """Verify a key that is not safely inside one project is not attributed to any."""
        assert ProjectKeys.owning(StorageKey(key)) is None
