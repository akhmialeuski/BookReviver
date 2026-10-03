"""The catalogue of processors, found through the entry point group ``bookreviver.processors``.

Plugin discovery is the job of ``app``, the one layer that may see every other, so ``services`` and the plugins know
nothing of how a plugin is found. A built-in plugin and one from an external package are registered the same way, in the
group of their ``pyproject.toml``, and so are indistinguishable here. ``importlib.metadata.entry_points`` lists the
registered names and ``EntryPoint.load`` imports each plugin's class.

A plugin that cannot be imported, because an optional group of dependencies such as ``bookreviver[cv]`` is not installed
on this machine, is left out with a warning, so the application starts on a machine that has no OpenCV and offers the
processors that can run there. A processor of a pool this process does not serve is left out too. A key registered under
a name that is not the key of its processor is an error, since a recipe would address it by one name and the version
records the other.
"""

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, override

from bookreviver.domain.errors import NotFoundError
from bookreviver.ports.processing import Processor, ProcessorCatalog

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from importlib.metadata import EntryPoint

    from bookreviver.domain.enums import WorkerPool
    from bookreviver.domain.values import ProcessorSpec
    from bookreviver.ports.processing import ProcessorSettings

# The entry point group the processors are registered in
PROCESSOR_GROUP: str = 'bookreviver.processors'

logger = logging.getLogger(__name__)


class EntryPointCatalog(ProcessorCatalog):
    """The processors registered as entry points, loaded once when the catalogue is built."""

    def __init__(
        self,
        *,
        pools: Collection[WorkerPool],
        registered: Collection[EntryPoint] | None = None,
        settings: ProcessorSettings | None = None,
    ) -> None:
        """Load the processors of the given pools.

        :param pools: Pools of workers this process serves; the processors of other pools are left out.
        :type pools: Collection[WorkerPool]
        :param registered: The entry points to load, or None for those of the group ``bookreviver.processors``.
        :type registered: Collection[EntryPoint] | None
        :param settings: What every loaded processor is told about the machine, or None to tell it nothing.
        :type settings: ProcessorSettings | None
        :raises ValueError: If a processor is registered under a name that is not its key.
        :raises TypeError: If an entry point registers something that is not a processor.
        """
        self._processors: dict[str, Processor] = {}
        for entry_point in registered if registered is not None else entry_points(group=PROCESSOR_GROUP):
            try:
                processor_class = entry_point.load()
            except ImportError:
                logger.warning(
                    'The processor %s is not offered, since what it needs is not installed.',
                    entry_point.name,
                    exc_info=True,
                )
                continue
            processor = processor_class()
            if not isinstance(processor, Processor):
                err_msg = f'The entry point {entry_point.name} registers {processor_class}, which is no processor.'
                raise TypeError(err_msg)
            if processor.spec.key != entry_point.name:
                err_msg = f'The entry point {entry_point.name} registers the processor {processor.spec.key}.'
                raise ValueError(err_msg)
            if processor.spec.pool in pools:
                if settings is not None:
                    processor.configure(settings)
                self._processors[entry_point.name] = processor

    @override
    def get(self, key: str) -> Processor:
        """Return the processor with this key.

        :param key: Key of the processor, such as ``geometry.deskew``.
        :type key: str
        :returns: The processor.
        :rtype: Processor
        :raises NotFoundError: If no loaded processor has this key.
        """
        if (processor := self._processors.get(key)) is None:
            raise NotFoundError(key)
        return processor

    @override
    def specs(self) -> Sequence[ProcessorSpec]:
        """List the specs of the loaded processors by key.

        :returns: The specs, ordered by key.
        :rtype: Sequence[ProcessorSpec]
        """
        return [self._processors[key].spec for key in sorted(self._processors)]
