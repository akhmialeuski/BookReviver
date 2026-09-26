"""Tests for the domain entities and values."""

import pytest
from attrs import evolve

from bookreviver.domain.enums import PageAsset
from bookreviver.domain.values import BookDetails, Progress
from tests.helpers.builders import make_page, make_project, new_account_id


class TestPageAssetKey:
    """Tests for Page.asset_key()."""

    def test_key_changes_with_asset_version(self) -> None:
        """Verify regenerated assets get new keys, so cached URLs never serve stale images."""
        page = make_page(project_id=make_project(owner_id=new_account_id()).id, index=4)
        regenerated = evolve(page, assets=evolve(page.assets, version=1))
        first, second = page.asset_key(PageAsset.TILES), regenerated.asset_key(PageAsset.TILES)
        assert (first.endswith('/pages/4/v0/iiif'), second.endswith('/pages/4/v1/iiif')) == (True, True)


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
        """Verify the fraction is done over total, and zero while the total is unknown."""
        assert Progress(done=done, total=total).fraction == fraction
