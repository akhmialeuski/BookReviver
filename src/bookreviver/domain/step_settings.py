"""The settings of the processing steps: the methods they choose between and their parameter names."""

from bookreviver.domain.labels import LabeledStrEnum


class Binarization(LabeledStrEnum):
    """How a page is made black and white to find its ink."""

    OTSU = 'otsu', 'One threshold for the page, found by the method of Otsu'
    ADAPTIVE = 'adaptive', 'A threshold for each neighbourhood, for a page lit unevenly'


class VerticalAlign(LabeledStrEnum):
    """Where a block of text stands between the top and the bottom margin of a page."""

    TOP = 'top', 'By the top margin'
    CENTER = 'center', 'In the middle'
    BOTTOM = 'bottom', 'By the bottom margin'


class HorizontalAlign(LabeledStrEnum):
    """Where a block of text stands between the side margins of a page."""

    INNER = 'inner', 'By the margin at the gutter'
    OUTER = 'outer', 'By the margin at the outer edge'
    LEFT = 'left', 'By the left margin'
    RIGHT = 'right', 'By the right margin'
    CENTER = 'center', 'In the middle'


class BlankParam(LabeledStrEnum):
    """Names of the parameters of the step ``pages.blank`` beyond the size, which a use case writes into a leaf."""

    FILL = 'fill', 'What fills the leaf'
    PAPER_FROM = 'paper_from', 'Versions of the pages the paper is taken from'


class PaperFill(LabeledStrEnum):
    """What colour fills the page round a block of text."""

    PAPER = 'paper', 'The median colour of the paper of the block'
    WHITE = 'white', 'White'


class PerspectiveMethod(LabeledStrEnum):
    """How ``geometry.perspective`` finds the sheet of paper on a scan."""

    PAPER_COLOUR = 'paper-colour', 'By the colour of the paper'
    EDGES = 'edges', 'By the edges of the sheet, for paper of the colour of its background'


class DeskewMethod(LabeledStrEnum):
    """How ``geometry.deskew`` finds the angle a page is turned by."""

    PROJECTION = 'projection', 'By the projection of the ink on the rows'
    HOUGH = 'hough', 'By the long straight lines, such as rules and frames'
    BASELINES = 'baselines', 'By the median slope of the baselines of the text'


class CropMethod(LabeledStrEnum):
    """How ``geometry.crop`` finds the frame of the content of a page.

    ``LAYOUT`` is declared so that recipes and clients can name it, but no processor offers it yet: it takes the frame
    from the regions of the Layout stage, which does not exist, so there are no regions for it to read. The day that
    stage records them, ``geometry.crop`` offers the method and nothing declared here changes.
    """

    INK_BLOCKS = 'ink-blocks', 'By the blocks of ink'
    LAYOUT = 'layout', 'By the regions of the Layout stage'


class DewarpMethod(LabeledStrEnum):
    """How ``geometry.dewarp`` finds the bend of a page.

    ``DOCRES`` is declared so that recipes and clients can name it, but no processor offers it: its model needs a
    graphics card, which the plugin of the optional group ``gpu`` brings, and that plugin does not exist yet.
    """

    TEXT_LINES = 'text-lines', 'By the curves of the lines of text'
    PAGE_EDGES = 'page-edges', 'By the top and bottom edges of the sheet, for pages with few lines'
    UVDOC = 'uvdoc', 'By the UVDoc neural network'
    DOCRES = 'docres', 'By the DocRes neural network'


class OutputMode(LabeledStrEnum):
    """What the page looks like after ``cleanup.binarize``."""

    BW = 'bw', 'Black and white'
    GRAY = 'gray', 'Grayscale with even light'
    COLOR = 'color', 'Colour with even light'
    MIXED = 'mixed', 'Black and white text, pictures in tones'


class BinarizationMethod(LabeledStrEnum):
    """How ``cleanup.binarize`` parts the ink of a page from its paper.

    ``NEURAL`` is declared for the plugin of a model that comes in the ``gpu`` group, and no step offers it yet.
    """

    OTSU = 'otsu', 'Otsu, one threshold for the page'
    SAUVOLA = 'sauvola', 'Sauvola, a threshold for each neighbourhood'
    WOLF = 'wolf', 'Wolf, Sauvola with the contrast of the page'
    ISAUVOLA = 'isauvola', 'ISauvola, Sauvola for a stained page'
    SU = 'su', 'Su, the edges of the strokes'
    GATOS = 'gatos', 'Gatos, a background surface under the ink'
    NICK = 'nick', 'NICK, for pages of pale ink'
    BRADLEY = 'bradley', 'Bradley, the mean of the neighbourhood'
    NEURAL = 'neural', 'Neural network'


class EraserFill(LabeledStrEnum):
    """What ``cleanup.eraser`` paints the erased area with."""

    WHITE = 'white', 'White'
    BLACK = 'black', 'Black'
    AROUND = 'around', 'The mean colour around the area'
