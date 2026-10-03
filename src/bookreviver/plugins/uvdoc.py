"""The UVDoc neural network, which predicts how a page is bent, run on the CPU with ONNX Runtime.

UVDoc (Verhoeven, Magne and Sorkine-Hornung, SIGGRAPH Asia 2023) takes a picture of a page shrunk to 496 by 720 pixels
and gives a grid of 45 rows by 31 columns that says, for each node of an even grid over the flat page, where in the
picture it is taken from, in the coordinates from -1 to 1 of the picture. The network is a ResNet of about 8 million
parameters, so one page takes a fraction of a second on the CPU, and the grid is turned into the field that remaps the
page at its own resolution.

The network is an ONNX export of the weights of UVDoc, kept in two files that have to lie side by side, the graph and
its weights. They are downloaded the first time a page is dewarped by the method into the models directory of the
settings, from a revision of the repository that is fixed here, and each is checked against its SHA-256 digest, so a
changed or a damaged file is never run. A file is written under a temporary name and put in its place when it is whole.
"""

import hashlib
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

import cv2
import httpx
import numpy as np
import onnxruntime
from attrs import frozen

from bookreviver.domain.errors import ConflictError
from bookreviver.plugins.cv_image import COLOR_PLANES

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.plugins.cv_image import Floats, Samples

# The revision of the repository of the export, so the files cannot change under a recipe
MODEL_REVISION: str = '39c8284d324446cfbbba3fd0695c2b3358fefaca'
MODEL_URL: str = 'https://huggingface.co/fredcallagan/uvdoc-grid-onnx/resolve/{revision}/{name}'
GRAPH_NAME: str = 'UVDoc_grid.onnx'
WEIGHTS_NAME: str = 'UVDoc_grid.onnx.data'
# The size of the picture the network takes, as the width and the height in pixels, and the name of its input
INPUT_SIZE_PX: tuple[int, int] = (496, 720)
INPUT_NAME: str = 'image'
# The largest sample of an 8-bit image, by which the samples are brought to the range from 0 to 1, and the range of the
# coordinates the network gives
SAMPLE_MAX: float = 255.0
# Seconds the download of a file may wait for the server, and the bytes of it read at a time
DOWNLOAD_TIMEOUT_S: float = 120.0
DOWNLOAD_CHUNK_BYTES: int = 1024 * 1024
PROVIDERS: tuple[str, ...] = ('CPUExecutionProvider',)
MODEL_UNAVAILABLE: str = 'The UVDoc model {name} could not be downloaded: {reason}.'
MODEL_CORRUPT: str = 'The UVDoc model {name} does not have the digest it is expected to have.'


@frozen(kw_only=True)
class ModelFile:
    """A file of the model.

    :ivar name: Name of the file, in the repository and in the models directory.
    :ivar sha256: SHA-256 digest of the file, in lower-case hexadecimal.
    """

    name: str
    sha256: str


MODEL_FILES: tuple[ModelFile, ...] = (
    ModelFile(name=GRAPH_NAME, sha256='044b406398e8accf2b8c896043f22d318221e443bb095c55d184e97f3eb003f7'),
    ModelFile(name=WEIGHTS_NAME, sha256='3a58a04944e59578200d7b0ed02ccb3ade934e9d88efa21454fbb8b53fa40f02'),
)


class UvDocModel:
    """The UVDoc network, loaded the first time it is used and then shared by every thread that dewarps a page."""

    def __init__(self, directory: Path) -> None:
        """Keep the directory the files of the model are looked for and downloaded into.

        :param directory: The models directory of the settings.
        :type directory: Path
        """
        self._directory = directory
        self._lock = threading.Lock()
        self._session: onnxruntime.InferenceSession | None = None

    @property
    def graph(self) -> Path:
        """The path of the graph of the network, which is where a test looks to tell whether the model is there."""
        return self._directory / GRAPH_NAME

    def grid(self, image: Samples) -> Floats:
        """Predict where each node of an even grid over the flat page is taken from in the picture.

        :param image: The samples of the bent page, gray or blue, green and red.
        :type image: Samples
        :returns: For each node, from the top left to the bottom right, the place in the picture as x and y in pixels,
                  in an array of 45 rows, 31 columns and two.
        :rtype: Floats
        :raises ConflictError: If the model cannot be downloaded or is not the one that is expected.
        """
        session = self._load()
        height, width = image.shape[:2]
        colour = cv2.cvtColor(image, cv2.COLOR_BGR2RGB if image.ndim == COLOR_PLANES else cv2.COLOR_GRAY2RGB)
        shrunk = cv2.resize(colour, INPUT_SIZE_PX, interpolation=cv2.INTER_AREA)
        blob = np.transpose(shrunk.astype(np.float32) / SAMPLE_MAX, (2, 0, 1))[None]
        grid = np.asarray(session.run(None, {INPUT_NAME: blob})[0], dtype=np.float64)[0]
        # The network gives -1 for the left and top edge of the picture and 1 for the right and bottom edge
        places = (np.transpose(grid, (1, 2, 0)) + 1) / 2 * np.array([width - 1, height - 1])
        return np.asarray(places, dtype=np.float64)

    def _load(self) -> onnxruntime.InferenceSession:
        """Download the files that are missing and open the graph, once.

        :returns: The session of ONNX Runtime.
        :rtype: onnxruntime.InferenceSession
        :raises ConflictError: If a file cannot be downloaded or has the wrong digest.
        """
        with self._lock:
            if self._session is None:
                self._directory.mkdir(parents=True, exist_ok=True)
                for file in MODEL_FILES:
                    if not (self._directory / file.name).is_file():
                        self._download(file)
                self._session = onnxruntime.InferenceSession(str(self.graph), providers=list(PROVIDERS))
            return self._session

    def _download(self, file: ModelFile) -> None:
        """Download a file of the model next to the others, keeping it only if its digest is the expected one.

        :param file: The file.
        :type file: ModelFile
        :raises ConflictError: If the download fails or the file is not the expected one.
        """
        target = self._directory / file.name
        part = target.with_name(f'{file.name}.part')
        digest = hashlib.sha256()
        url = MODEL_URL.format(revision=MODEL_REVISION, name=file.name)
        try:
            with (
                httpx.stream('GET', url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as response,
                part.open('wb') as out,
            ):
                response.raise_for_status()
                for chunk in response.iter_bytes(DOWNLOAD_CHUNK_BYTES):
                    digest.update(chunk)
                    out.write(chunk)
        except (httpx.HTTPError, OSError) as error:
            with suppress(OSError):
                part.unlink()
            raise ConflictError(MODEL_UNAVAILABLE.format(name=file.name, reason=error)) from error
        if digest.hexdigest() != file.sha256:
            part.unlink()
            raise ConflictError(MODEL_CORRUPT.format(name=file.name))
        part.replace(target)
