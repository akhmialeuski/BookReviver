"""Tests for the processing endpoints, on the application's own adapters with the fake processors of the tests.

The stores, the imaging adapters and the in-process broker are the application's own, so the files of a version are
written by libvips as they are for a user, and a test waits for the broker to finish the jobs a request queued.
"""

import io
import json
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from PIL import Image
from pydantic import ValidationError
from taskiq import AsyncBroker, InMemoryBroker

from bookreviver.api.schemas.edits import EditForm
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.processing import (
    PageStageSchema,
    PageVersionSchema,
    ProcessorSchema,
    RecipeSchema,
    StageRunBody,
)
from bookreviver.api.schemas.rules import RecipeRuleSchema
from bookreviver.api.schemas.types import RECIPE_STEPS_MAX_LENGTH
from bookreviver.domain.enums import (
    EditorKind,
    JobKind,
    JobState,
    Rendition,
    RuleCondition,
    Stage,
    StageState,
    VersionScale,
)
from bookreviver.domain.geometry import Line, Mesh, SplitChoice
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions, StageRun
from tests.helpers.builders import make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import AsyncContainer, Provider

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROCESSORS_PATH: str = '/api/v1/processors'
MEASURE_SUFFIX: str = '/stages/geometry/measure'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
FAKE_KEY: str = 'geometry.fake'
SCAN_SIZE_PX: tuple[int, int] = (120, 160)
ITEMS: str = 'items'


class Book(NamedTuple):
    """A book of the signed-in account with one page cut from a scan whose image is stored.

    :ivar project: The project of the book.
    :ivar page: The page the import made of the scan.
    """

    project: Project
    page: Page

    @property
    def path(self) -> str:
        """The path of the project."""
        return f'{PROJECTS_PATH}/{self.project.id}'

    @property
    def page_path(self) -> str:
        """The path of the page."""
        return f'{self.path}/pages/{self.page.id}'


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the fakes of the tests.

    :returns: The provider of the fake catalogue and recipes.
    :rtype: Sequence[Provider]
    """
    return [ProcessingFakesProvider()]


@pytest.fixture
async def fx_broker(fx_container: AsyncContainer) -> InMemoryBroker:
    """Return the broker the application runs its jobs on, which a test waits on until they have finished.

    :param fx_container: Container of the running application.
    :type fx_container: AsyncContainer
    :returns: The started in-process broker.
    :rtype: InMemoryBroker
    """
    broker = await fx_container.get(AsyncBroker)
    assert isinstance(broker, InMemoryBroker)
    return broker


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_asset_store: AssetStore, fx_actor: Actor) -> Book:
    """Commit a book of one page and store the full image of its scan as a real JPEG.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Asset store of the application.
    :type fx_asset_store: AssetStore
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    source = make_source(project_id=project.id)
    scan = evolve(
        make_scan(source=source, number=0),
        renditions=Renditions(ready=True),
    )
    page = make_page(project_id=project.id, scan=scan)
    await commit_project(fx_database, project, page, sources=[source], scans=[scan])
    keys = ProjectKeys(project.id)
    buffer = io.BytesIO()
    Image.new('L', SCAN_SIZE_PX, color=200).save(buffer, format='JPEG')
    for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
        async with fx_asset_store.writable(keys.scan_rendition(scan, rendition)) as path:
            path.write_bytes(buffer.getvalue())
    return Book(project=project, page=page)


async def run_stage(client: httpx.AsyncClient, broker: InMemoryBroker, book: Book, stage: str) -> JobSchema:
    """Ask for a run of a stage and wait for the job to finish.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param broker: In-process broker running the job.
    :type broker: InMemoryBroker
    :param book: Book of the signed-in account.
    :type book: Book
    :param stage: The stage to run.
    :type stage: str
    :returns: The job as the request answered with it.
    :rtype: JobSchema
    """
    response = await client.post(f'{book.path}/stages/{stage}/run', json={})
    assert response.status_code == status.HTTP_202_ACCEPTED
    await broker.wait_all()
    return JobSchema.model_validate_json(response.content)


class TestProcessors:
    """Tests for GET /processors."""

    async def test_lists_the_processors_with_the_schema_of_their_parameters(self, fx_client: httpx.AsyncClient) -> None:
        """Verify every processor of the catalogue is listed with the JSON Schema of its parameters.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(PROCESSORS_PATH)
        listed = [ProcessorSchema.model_validate(item) for item in response.json()[ITEMS]]
        expect(response.status_code == status.HTTP_200_OK)
        expect([processor.key for processor in listed] == ['cleanup.fake', FAKE_KEY, 'split.none'])
        expect(
            all(processor.parameters.get('type') == 'object' for processor in listed if processor.key == 'split.none')
        )
        assert_expectations()


class TestRecipes:
    """Tests for the recipe and variant endpoints."""

    async def test_active_recipe_is_the_default_of_the_stage(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the first request of a stage creates its default recipe.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{fx_book.path}/stages/geometry/recipe')
        recipe = RecipeSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((recipe.active, [step.processor_key for step in recipe.steps]) == (True, [FAKE_KEY]))
        assert_expectations()

    async def test_put_replaces_the_steps_and_fills_in_the_defaults(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the recipe is saved with the checked parameters of each step.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'name': 'Strong', 'steps': [{'processor_key': FAKE_KEY, 'params': {'strength': 4}}]}
        response = await fx_client.put(f'{fx_book.path}/stages/geometry/recipe', json=body)
        recipe = RecipeSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((recipe.name, recipe.steps[0].params) == ('Strong', {'strength': 4, 'fail': False}))
        assert_expectations()

    async def test_steps_that_do_not_fit_are_a_422_problem(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a parameter the processor does not know answers 422 with a problem document.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'name': 'Broken', 'steps': [{'processor_key': FAKE_KEY, 'params': {'unknown': 1}}]}
        response = await fx_client.put(f'{fx_book.path}/stages/geometry/recipe', json=body)
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect('Unknown parameters' in response.json()['detail'])
        assert_expectations()

    async def test_variant_is_created_listed_and_activated(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a variant is answered 201, listed after the active recipe, and swaps places on activation.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'name': 'Strong', 'steps': [{'processor_key': FAKE_KEY}]}
        created = await fx_client.post(f'{fx_book.path}/stages/geometry/variants', json=body)
        variant = RecipeSchema.model_validate_json(created.content)
        activated = await fx_client.post(f'{fx_book.path}/stages/geometry/variants/{variant.id}/activate')
        listed = await fx_client.get(f'{fx_book.path}/stages/geometry/variants')
        recipes = [RecipeSchema.model_validate(item) for item in listed.json()[ITEMS]]
        expect((created.status_code, variant.active) == (status.HTTP_201_CREATED, False))
        expect(
            activated.status_code == status.HTTP_200_OK and RecipeSchema.model_validate_json(activated.content).active
        )
        expect([(recipe.name, recipe.active) for recipe in recipes] == [('Strong', True), ('Fake', False)])
        assert_expectations()

    async def test_variant_can_be_saved(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify PUT on a variant replaces its steps.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'name': 'Strong', 'steps': [{'processor_key': FAKE_KEY}]}
        created = await fx_client.post(f'{fx_book.path}/stages/geometry/variants', json=body)
        variant = RecipeSchema.model_validate_json(created.content)
        body['name'] = 'Stronger'
        saved = await fx_client.put(f'{fx_book.path}/stages/geometry/variants/{variant.id}', json=body)
        assert RecipeSchema.model_validate_json(saved.content).name == 'Stronger'

    async def test_project_of_another_account_is_not_found(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a project the account does not own is answered 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(f'{PROJECTS_PATH}/{new_account_id()}/stages/geometry/recipe')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_stage_that_does_not_exist_is_a_422(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a stage outside the pipeline is refused by the path.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{fx_book.path}/stages/nowhere/recipe')
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestRunAndVersions:
    """Tests for running a stage, and for reading the stages and versions it made."""

    async def test_run_answers_202_and_the_job_makes_a_current_version_with_served_images(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the run is a job, the stage names its current version, and the version's images are served.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        job = await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        stages = await fx_client.get(f'{fx_book.page_path}/stages')
        [stage] = [PageStageSchema.model_validate(item) for item in stages.json()[ITEMS]]
        assert stage.head_version_id is not None
        version_response = await fx_client.get(f'{fx_book.page_path}/versions/{stage.head_version_id}')
        version = PageVersionSchema.model_validate_json(version_response.content)
        assert version.images is not None
        full = await fx_client.get(version.images.full)
        info = await fx_client.get(version.images.iiif_info)
        with Image.open(io.BytesIO(full.content)) as image:
            size = image.size
        expect((job.kind, job.state) == (JobKind.RUN_STAGE, JobState.QUEUED))
        expect(stage.state is StageState.FRESH)
        expect((version.processor.key, version.tiles_ready, version.error) == ('split.none', True, ''))
        expect(version.data == {'width_px': 2200, 'height_px': 1561})
        expect(size == SCAN_SIZE_PX)
        expect(info.status_code == status.HTTP_200_OK)
        assert_expectations()

    async def test_second_run_while_one_is_active_is_a_409_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a run answers 409 while another run of the project is queued.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        first = await fx_client.post(f'{fx_book.path}/stages/page-split/run', json={})
        second = await fx_client.post(f'{fx_book.path}/stages/page-split/run', json={})
        expect(first.status_code == status.HTTP_202_ACCEPTED)
        expect(second.status_code == status.HTTP_409_CONFLICT)
        assert_expectations()

    async def test_versions_are_listed_with_a_filter(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the list of versions filters by scale, and a version that does not exist is a 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        full = await fx_client.get(f'{fx_book.page_path}/versions', params={'scale': VersionScale.FULL.value})
        previews = await fx_client.get(f'{fx_book.page_path}/versions', params={'scale': VersionScale.PREVIEW.value})
        missing = await fx_client.get(f'{fx_book.page_path}/versions/{"0" * 16}')
        expect((full.json()['total'], previews.json()['total']) == (1, 0))
        expect(missing.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()

    async def test_malformed_version_identifier_is_a_422(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify an identifier that is not 16 hexadecimal digits is refused by the path.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{fx_book.page_path}/versions/not-a-version')
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_choosing_a_version_makes_it_current(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify PUT on a stage with a version of the page makes it the current one, and a stranger is refused.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        listed = await fx_client.get(f'{fx_book.page_path}/versions')
        version_id = listed.json()[ITEMS][0]['id']
        chosen = await fx_client.put(f'{fx_book.page_path}/stages/page-split', json={'version_id': version_id})
        wrong = await fx_client.put(f'{fx_book.page_path}/stages/geometry', json={'version_id': version_id})
        expect(chosen.status_code == status.HTTP_200_OK)
        expect(PageStageSchema.model_validate_json(chosen.content).head_version_id == version_id)
        expect(wrong.status_code == status.HTTP_409_CONFLICT)
        assert_expectations()

    async def test_preview_makes_a_preview_version_that_has_only_its_preview_path(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify a preview is a job whose result is a preview-scale version with the path of its preview image.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        body = {
            'page_id': str(fx_book.page.id),
            'steps': [{'processor_key': FAKE_KEY, 'params': {'strength': 3}}],
            'step_index': 0,
        }
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/preview', json=body)
        await fx_broker.wait_all()
        previews = await fx_client.get(f'{fx_book.page_path}/versions', params={'scale': 'preview'})
        [version] = [PageVersionSchema.model_validate(item) for item in previews.json()[ITEMS]]
        assert version.preview is not None
        served = await fx_client.get(version.preview)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect((version.images, served.status_code) == (None, status.HTTP_200_OK))
        assert_expectations()

    @pytest.mark.parametrize(
        'body',
        [
            {'page_id': 'not-a-uuid', 'steps': [{'processor_key': FAKE_KEY}], 'step_index': 0},
            {'page_id': '00000000-0000-0000-0000-000000000000', 'steps': [], 'step_index': 0},
            {
                'page_id': '00000000-0000-0000-0000-000000000000',
                'steps': [{'processor_key': FAKE_KEY}],
                'step_index': 1,
            },
        ],
        ids=['page', 'no-steps', 'index-past-the-steps'],
    )
    async def test_preview_body_that_does_not_validate_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, object]
    ) -> None:
        """Verify the body of a preview is checked before the route runs, the index against the steps included.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: Body under test.
        :type body: dict[str, object]
        """
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/preview', json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_collection_is_a_202_job(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a collection is queued as a job and answered 202.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(f'{fx_book.path}/versions/collect')
        job = JobSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect(job.kind is JobKind.COLLECT_VERSIONS)
        assert_expectations()

    async def test_measuring_the_book_is_a_202_job(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the measure of the book is queued as a job and answered 202.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(f'{fx_book.path}{MEASURE_SUFFIX}')
        job = JobSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect((job.kind, job.state) == (JobKind.MEASURE_BOOK, JobState.QUEUED))
        assert_expectations()

    async def test_measuring_the_book_while_a_run_is_active_is_a_409_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the measure is refused while a run of the project is queued, which may be writing what it reads.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        run = await fx_client.post(f'{fx_book.path}/stages/page-split/run', json={})
        measure = await fx_client.post(f'{fx_book.path}{MEASURE_SUFFIX}')
        expect(run.status_code == status.HTTP_202_ACCEPTED)
        expect(measure.status_code == status.HTTP_409_CONFLICT)
        assert_expectations()

    async def test_measuring_the_book_of_another_account_is_a_404(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a project that is not the account's is reported like a missing one.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.post(f'{PROJECTS_PATH}/{uuid4()}{MEASURE_SUFFIX}')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_tiles_of_a_version_are_cut_on_request(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the tiles endpoint queues a job for a ready version, and answers 404 for one that does not exist.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        listed = await fx_client.get(f'{fx_book.page_path}/versions')
        version_id = listed.json()[ITEMS][0]['id']
        accepted = await fx_client.post(f'{fx_book.page_path}/versions/{version_id}/tiles')
        missing = await fx_client.post(f'{fx_book.page_path}/versions/{"f" * 16}/tiles')
        expect(accepted.status_code == status.HTTP_202_ACCEPTED)
        expect(missing.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()


async def active_step_id(client: httpx.AsyncClient, book: Book, stage: Stage) -> str:
    """Read the identifier of the only step of the active recipe of a stage, which an edit is addressed by.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: Book of the signed-in account.
    :type book: Book
    :param stage: The stage whose active recipe is read.
    :type stage: Stage
    :returns: The identifier of the step.
    :rtype: str
    """
    recipe = RecipeSchema.model_validate_json((await client.get(f'{book.path}/stages/{stage}/recipe')).content)
    return str(recipe.steps[0].step_id)


class TestEdits:
    """Tests for the manual edit endpoints."""

    async def test_edit_is_saved_listed_and_deleted(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the form of an edit is stored with its hash, listed, and removed with a 204.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        step_id = await active_step_id(fx_client, fx_book, Stage.GEOMETRY)
        path = f'{fx_book.page_path}/edits/geometry/{step_id}'
        saved = await fx_client.put(path, data={'kind': 'rotation', 'geometry': '{"degrees": 1.5}'})
        listed = await fx_client.get(f'{fx_book.page_path}/edits/geometry')
        deleted = await fx_client.delete(path)
        after = await fx_client.get(f'{fx_book.page_path}/edits/geometry')
        expect(saved.status_code == status.HTTP_200_OK)
        expect(saved.json()['geometry'] == {'degrees': 1.5})
        expect(saved.json()['step_id'] == step_id)
        expect([item['edit_hash'] for item in listed.json()[ITEMS]] == [saved.json()['edit_hash']])
        expect((deleted.status_code, after.json()['total']) == (status.HTTP_204_NO_CONTENT, 0))
        assert_expectations()

    @pytest.mark.parametrize(
        'data',
        [
            {'kind': 'rotation', 'geometry': '{"angle": 1}'},
            {'kind': 'rotation', 'geometry': 'not json'},
            {'kind': 'rect', 'geometry': '{"left": 0, "top": 0, "width": 1, "height": 1}'},
            {'kind': 'rotation'},
        ],
        ids=['wrong-shape', 'not-json', 'other-editor', 'no-shape'],
    )
    async def test_edit_that_does_not_fit_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, data: dict[str, str]
    ) -> None:
        """Verify a shape that does not fit its editor, and an edit the processor does not read, answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param data: Form under test.
        :type data: dict[str, str]
        """
        step_id = await active_step_id(fx_client, fx_book, Stage.GEOMETRY)
        response = await fx_client.put(f'{fx_book.page_path}/edits/geometry/{step_id}', data=data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_edit_of_a_step_that_no_recipe_has_is_a_404(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify an edit addressed to an identifier that is the step of no recipe of the stage answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(
            f'{fx_book.page_path}/edits/geometry/{uuid4()}', data={'kind': 'rotation', 'geometry': '{"degrees": 1}'}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_brush_edit_takes_its_mask_as_a_file(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a mask sent with the form is stored and served from the path the edit names.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        path = f'{fx_book.page_path}/edits/cleanup/{await active_step_id(fx_client, fx_book, Stage.CLEANUP)}'
        saved = await fx_client.put(path, data={'kind': 'brush-mask'}, files={'mask': ('mask.png', b'mask-bytes')})
        served = await fx_client.get(saved.json()['mask'])
        expect(saved.status_code == status.HTTP_200_OK)
        expect((served.status_code, served.content) == (status.HTTP_200_OK, b'mask-bytes'))
        assert_expectations()


class TestPageSettings:
    """Tests for the endpoints of the settings a page has for a step of a recipe."""

    async def test_field_is_set_listed_and_taken_back(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a field is stored for the page with the value the processor returns, listed, and removed with a 204.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        step_id = await active_step_id(fx_client, fx_book, Stage.GEOMETRY)
        path = f'{fx_book.page_path}/settings/geometry/{step_id}/strength'
        saved = await fx_client.put(path, json={'value': 2})
        listed = await fx_client.get(f'{fx_book.page_path}/settings/geometry')
        deleted = await fx_client.delete(path)
        after = await fx_client.get(f'{fx_book.page_path}/settings/geometry')
        expect(saved.status_code == status.HTTP_200_OK)
        expect(saved.json()['params'] == {'strength': 2})
        expect(saved.json()['step_id'] == step_id)
        expect([item['params'] for item in listed.json()[ITEMS]] == [{'strength': 2}])
        expect((deleted.status_code, after.json()['total']) == (status.HTTP_204_NO_CONTENT, 0))
        assert_expectations()

    @pytest.mark.parametrize(
        ('name', 'body'),
        [('no_such_field', {'value': 1}), ('strength', {})],
        ids=['unknown-field', 'no-value'],
    )
    async def test_setting_that_does_not_fit_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, name: str, body: dict[str, int]
    ) -> None:
        """Verify a field the processor does not have, and a request with no value, answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param name: Name of the field in the address.
        :type name: str
        :param body: Body under test.
        :type body: dict[str, int]
        """
        step_id = await active_step_id(fx_client, fx_book, Stage.GEOMETRY)
        response = await fx_client.put(f'{fx_book.page_path}/settings/geometry/{step_id}/{name}', json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_setting_of_a_step_that_no_recipe_has_is_a_404(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a field addressed to an identifier that is the step of no recipe of the stage answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(f'{fx_book.page_path}/settings/geometry/{uuid4()}/strength', json={'value': 2})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_taking_back_a_field_the_page_does_not_change_is_a_404(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify taking back a field that was never set answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        step_id = await active_step_id(fx_client, fx_book, Stage.GEOMETRY)
        response = await fx_client.delete(f'{fx_book.page_path}/settings/geometry/{step_id}/strength')
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestEditForm:
    """Tests for the form of an edit, for the shapes the split editor draws."""

    def test_a_choice_of_one_page_or_two_is_read_into_its_shape(self) -> None:
        """Verify the form of the split editor gives the choice, with the line of a cut when there is one."""
        line = {'start': {'x': 800.0, 'y': 0.0}, 'end': {'x': 790.0, 'y': 1200.0}}
        plain = EditForm.model_validate({'kind': EditorKind.SPLIT, 'geometry': '{"pages": 2}'}).to_edit()
        drawn = EditForm.model_validate(
            {'kind': EditorKind.SPLIT, 'geometry': json.dumps({'pages': 2, 'line': line})}
        ).to_edit()
        expect(plain.geometry == SplitChoice(pages=SplitChoice.TWO_PAGES))
        expect(isinstance(drawn.geometry, SplitChoice) and drawn.geometry.line == Line.from_data(line))
        assert_expectations()

    def test_the_curves_of_the_mesh_editor_are_read_into_a_mesh(self) -> None:
        """Verify the form of the mesh editor gives a mesh of the rows of nodes it was sent."""
        rows = [[{'x': 0.0, 'y': 100.0}, {'x': 900.0, 'y': 140.0}], [{'x': 0.0, 'y': 800.0}, {'x': 900.0, 'y': 850.0}]]
        edit = EditForm.model_validate({'kind': EditorKind.MESH, 'geometry': json.dumps({'rows': rows})}).to_edit()
        assert edit.geometry == Mesh.from_data({'rows': rows})

    @pytest.mark.parametrize(
        'geometry',
        [
            '{"rows": []}',
            '{"rows": [[{"x": 0, "y": 1}, {"x": 1, "y": 1}]]}',
            '{"pages": 2}',
            '{"rows": [[1, 2], [3, 4]]}',
        ],
        ids=['no-rows', 'one-row', 'a-split-choice', 'nodes-that-are-no-points'],
    )
    def test_curves_that_do_not_fit_are_refused(self, geometry: str) -> None:
        """Reject no rows, a single row, the shape of another editor, and nodes that are not points.

        :param geometry: The shape under test, as the form sends it.
        :type geometry: str
        """
        with pytest.raises(ValidationError):
            EditForm.model_validate({'kind': EditorKind.MESH, 'geometry': geometry})

    @pytest.mark.parametrize('geometry', ['{"pages": 3}', '{"line": null}', '{"pages": 1, "line": {"start": 1}}'])
    def test_a_choice_that_does_not_fit_is_refused(self, geometry: str) -> None:
        """Reject a number of pages other than one or two, a choice with no pages, and a line with no points.

        :param geometry: The shape under test, as the form sends it.
        :type geometry: str
        """
        with pytest.raises(ValidationError):
            EditForm.model_validate({'kind': EditorKind.SPLIT, 'geometry': geometry})


class TestStageRunBody:
    """Tests for the body of a run of a stage."""

    @pytest.mark.parametrize('confirmed', [True, False], ids=['confirmed', 'not-confirmed'])
    def test_the_confirmation_of_an_unsplit_reaches_the_run_and_the_job_that_stores_it(
        self, *, confirmed: bool
    ) -> None:
        """Verify the confirmation is in the run the body states, and survives being stored as the parameters of a job.

        :param confirmed: Whether the body confirms that undoing a split deletes the right half.
        :type confirmed: bool
        """
        run = StageRunBody(confirm_unsplit=confirmed).to_run(Stage.PAGE_SPLIT)
        assert (run.confirm_unsplit, StageRun.from_map(run.to_map()).confirm_unsplit) == (confirmed, confirmed)


async def create_variant(client: httpx.AsyncClient, book: Book, name: str) -> RecipeSchema:
    """Add a variant of the geometry stage through the API.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: Book of the signed-in account.
    :type book: Book
    :param name: Name of the variant.
    :type name: str
    :returns: The variant as the request answered with it.
    :rtype: RecipeSchema
    """
    created = await client.post(
        f'{book.path}/stages/geometry/variants', json={'name': name, 'steps': [{'processor_key': FAKE_KEY}]}
    )
    return RecipeSchema.model_validate_json(created.content)


class TestRules:
    """Tests for the rules that send the pages of a stage to its variants."""

    async def test_rule_is_created_listed_retargeted_and_deleted(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a rule is answered 201, listed with its place, sent to another recipe by PUT, and deleted with 204.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        plates = await create_variant(fx_client, fx_book, 'Plates')
        soft = await create_variant(fx_client, fx_book, 'Soft')
        rules_path = f'{fx_book.path}/stages/geometry/rules'
        created = await fx_client.post(rules_path, json={'condition': 'plates', 'recipe_id': str(plates.id)})
        rule = RecipeRuleSchema.model_validate_json(created.content)
        retargeted = await fx_client.put(f'{rules_path}/{rule.id}', json={'recipe_id': str(soft.id)})
        listed = await fx_client.get(rules_path)
        deleted = await fx_client.delete(f'{rules_path}/{rule.id}')
        after = await fx_client.get(rules_path)
        expect(created.status_code == status.HTTP_201_CREATED)
        expect((rule.condition, rule.order, rule.group_label) == (RuleCondition.PLATES, 0, ''))
        expect(RecipeRuleSchema.model_validate_json(retargeted.content).recipe_id == soft.id)
        expect([item['recipe_id'] for item in listed.json()[ITEMS]] == [str(soft.id)])
        expect(deleted.status_code == status.HTTP_204_NO_CONTENT)
        expect(after.json()['total'] == 0)
        assert_expectations()

    async def test_a_second_rule_for_a_condition_is_a_409_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the stage keeps one rule for each condition.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        plates = await create_variant(fx_client, fx_book, 'Plates')
        body = {'condition': 'plates', 'recipe_id': str(plates.id)}
        await fx_client.post(f'{fx_book.path}/stages/geometry/rules', json=body)
        second = await fx_client.post(f'{fx_book.path}/stages/geometry/rules', json=body)
        expect(second.status_code == status.HTTP_409_CONFLICT)
        expect(second.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_a_recipe_of_another_stage_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a rule of the cleanup stage cannot name a recipe of the geometry stage.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        plates = await create_variant(fx_client, fx_book, 'Plates')
        response = await fx_client.post(
            f'{fx_book.path}/stages/cleanup/rules', json={'condition': 'plates', 'recipe_id': str(plates.id)}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        'body',
        [
            {'condition': 'group'},
            {'condition': 'plates', 'group_label': 'Engravings'},
            {'condition': 'by-colour'},
            {'condition': 'plates', 'recipe_id': 'not-an-id'},
        ],
        ids=['group-without-label', 'label-without-group', 'unknown-condition', 'malformed-recipe'],
    )
    async def test_a_rule_that_does_not_validate_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, str]
    ) -> None:
        """Verify the body is checked before any rule is made.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: A body that is not valid.
        :type body: dict[str, str]
        """
        plates = await create_variant(fx_client, fx_book, 'Plates')
        response = await fx_client.post(
            f'{fx_book.path}/stages/geometry/rules', json={'recipe_id': str(plates.id), **body}
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_project_of_another_account_is_not_found(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the rules of a project the account does not own are answered 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(f'{PROJECTS_PATH}/{new_account_id()}/stages/geometry/rules')
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPinAndGroups:
    """Tests for the pin of a variant on a page, the counts of the variants and the group of a page."""

    async def test_a_run_by_a_variant_pins_it_when_asked_and_unpin_takes_it_off(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the pin is in the stage records and in the summary, and DELETE on the pin hands the page back.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await run_stage(fx_client, fx_broker, fx_book, 'page-split')
        plates = await create_variant(fx_client, fx_book, 'Plates')
        run = await fx_client.post(
            f'{fx_book.path}/stages/geometry/run',
            json={'recipe_id': str(plates.id), 'page_ids': [str(fx_book.page.id)], 'pin': True},
        )
        await fx_broker.wait_all()
        stages = await fx_client.get(f'{fx_book.page_path}/stages')
        pinned = {item['stage']: item for item in stages.json()[ITEMS]}['geometry']
        summary = await fx_client.get(f'{fx_book.path}/stages')
        geometry = {item['stage']: item for item in summary.json()[ITEMS]}['geometry']
        rows = await fx_client.get(f'{fx_book.path}/stages/geometry/pages')
        unpinned = await fx_client.delete(f'{fx_book.page_path}/stages/geometry/pin')
        expect(run.status_code == status.HTTP_202_ACCEPTED)
        expect((pinned['pinned'], pinned['recipe_id']) == (True, str(plates.id)))
        expect(geometry['variants'] == [{'recipe_id': str(plates.id), 'pages': 1}])
        expect([row['pinned'] for row in rows.json()[ITEMS]] == [True])
        expect(unpinned.status_code == status.HTTP_200_OK)
        expect(PageStageSchema.model_validate_json(unpinned.content).pinned is False)
        assert_expectations()

    async def test_unpinning_a_stage_that_has_not_run_is_a_404(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify there is no pin to take off a stage the page has not been through.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.delete(f'{fx_book.page_path}/stages/geometry/pin')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_run_pins_only_with_a_recipe(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a run that pins and names no recipe is refused by the body.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/run', json={'pin': True})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    @pytest.mark.parametrize(
        'through_step', [-1, 'first', RECIPE_STEPS_MAX_LENGTH], ids=['negative', 'text', 'too-large']
    )
    async def test_a_run_through_a_step_that_is_not_an_index_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, through_step: object
    ) -> None:
        """Verify the body of a run checks the index of the last step before the route runs.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param through_step: Value under test.
        :type through_step: object
        """
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/run', json={'through_step': through_step})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_run_through_a_step_the_recipe_does_not_have_is_a_409(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the recipe is asked, and a step past its last one is refused with the reason and no job is queued.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/stages/geometry/run', json={'through_step': RECIPE_STEPS_MAX_LENGTH - 1}
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_a_run_through_the_first_step_is_a_202_job(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a run that stops at the first step is queued like any other run.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/run', json={'through_step': 0})
        assert response.status_code == status.HTTP_202_ACCEPTED

    async def test_the_group_label_of_a_page_is_set_and_cleared(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a patch sets the label of the group, and a null clears it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        grouped = await fx_client.patch(fx_book.page_path, json={'group_label': 'Engravings'})
        cleared = await fx_client.patch(fx_book.page_path, json={'group_label': None})
        expect(grouped.json()['group_label'] == 'Engravings')
        expect(cleared.json()['group_label'] == '')
        assert_expectations()
