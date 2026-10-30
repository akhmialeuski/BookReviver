"""Contract of the StepExecutor port, run against every adapter listed in ``StepExecutorAdapter``."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.versions import VersionScale
from tests.helpers.step_executors import (
    FAILING_KEY,
    FAILING_MESSAGE,
    METHOD_KEY,
    PREVIEW_METHOD,
    PROBE_KEY,
    RUN_METHOD,
    make_step_input,
    open_executor_under_test,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.helpers.step_executors import StepExecutorAdapter

pytestmark = pytest.mark.anyio

MISSING_KEY: str = 'geometry.missing'


class TestStepExecutor:
    """Contract of StepExecutor.run()."""

    @pytest.mark.parametrize(
        ('scale', 'method'),
        [(VersionScale.FULL, RUN_METHOD), (VersionScale.PREVIEW, PREVIEW_METHOD)],
        ids=['full-run', 'preview'],
    )
    async def test_the_scale_chooses_the_method_of_the_processor(
        self, fx_step_executor_adapter: StepExecutorAdapter, tmp_path: Path, scale: VersionScale, method: str
    ) -> None:
        """Verify a full run calls ``Processor.run`` and a preview calls ``Processor.preview``.

        :param fx_step_executor_adapter: Adapter under test.
        :type fx_step_executor_adapter: StepExecutorAdapter
        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        :param scale: Scale of the step.
        :type scale: VersionScale
        :param method: Name of the method that must have run.
        :type method: str
        """
        async with open_executor_under_test(fx_step_executor_adapter) as executor:
            result = await executor.run(processor_key=PROBE_KEY, scale=scale, step_input=make_step_input(tmp_path))
        [output] = result.outputs
        assert output.data[METHOD_KEY] == method

    async def test_an_unknown_key_is_not_found(
        self, fx_step_executor_adapter: StepExecutorAdapter, tmp_path: Path
    ) -> None:
        """Verify a key that no processor has is reported as not found.

        :param fx_step_executor_adapter: Adapter under test.
        :type fx_step_executor_adapter: StepExecutorAdapter
        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        async with open_executor_under_test(fx_step_executor_adapter) as executor:
            with pytest.raises(NotFoundError):
                await executor.run(
                    processor_key=MISSING_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path)
                )

    async def test_a_domain_error_of_the_processor_arrives_with_its_type_and_message(
        self, fx_step_executor_adapter: StepExecutorAdapter, tmp_path: Path
    ) -> None:
        """Verify the error a processor raises reaches the caller as the same class with the same message.

        :param fx_step_executor_adapter: Adapter under test.
        :type fx_step_executor_adapter: StepExecutorAdapter
        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        async with open_executor_under_test(fx_step_executor_adapter) as executor:
            with pytest.raises(InvalidParametersError) as raised:
                await executor.run(
                    processor_key=FAILING_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path)
                )
        assert str(raised.value) == FAILING_MESSAGE
