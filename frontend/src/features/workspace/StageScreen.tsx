import { useQuery } from '@tanstack/react-query';
import { TriangleAlertIcon } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import type { PageSchema, ScanSchema, Stage, StagePageSchema, StepFlag } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { EDITOR_ROOM_SHARE } from '@/features/editors/scene';
import { useEditorSession } from '@/features/editors/useEditorSession';
import { useManifest } from '@/features/pages/manifest';
import { pruneSelection } from '@/features/pages/selection';
import { CompareCanvas } from '@/features/processing/CompareCanvas';
import {
  beforeSourceOf,
  type ImageSource,
  SourceKind,
  sourceOfPreview,
  sourceOfResult,
} from '@/features/processing/compare';
import { ProcessingPanel } from '@/features/processing/ProcessingPanel';
import { useAllScans } from '@/features/processing/queries';
import { reasonWithStep } from '@/features/processing/reasons';
import { StageBanners } from '@/features/processing/StageBanners';
import { wideScanIds } from '@/features/processing/split';
import { useEarlierRows } from '@/features/processing/useEarlierRows';
import { useProcessing } from '@/features/processing/useProcessing';
import { useShownStep } from '@/features/processing/useShownStep';
import { applyVariant, markOf, optionsOf } from '@/features/processing/variants';
import { stageBefore } from '@/features/stages/stages';
import { PageCanvas, type PageCanvasHandle } from '@/features/viewer/PageCanvas';
import {
  lastViewStart,
  nextViewStart,
  previousViewStart,
  viewIndexes,
  viewStart,
} from '@/features/viewer/spread';
import { FitMode, type StagePage } from '@/features/viewer/stage';
import { useViewerKeys } from '@/features/viewer/useViewerKeys';
import { CanvasToolbar } from '@/features/workspace/CanvasToolbar';
import { GridOverlay } from '@/features/workspace/GridOverlay';
import { useGrid, useGridKey } from '@/features/workspace/grid';
import { CompareMode, PageFilter, type StageSearch, ViewMode } from '@/features/workspace/params';
import { useStageRows, useStageSummaries, useStepRows } from '@/features/workspace/queries';
import { StageGrid } from '@/features/workspace/StageGrid';
import { StagePanel } from '@/features/workspace/StagePanel';
import { StageStrip } from '@/features/workspace/StageStrip';
import { StageWorkspace } from '@/features/workspace/StageWorkspace';
import { StepBar } from '@/features/workspace/StepBar';
import { StepCatalogue } from '@/features/workspace/StepCatalogue';
import { StepsWindow } from '@/features/workspace/StepsWindow';
import {
  type ClickModifiers,
  type SelectionState,
  selectionAfterClick,
} from '@/features/workspace/selection';
import { hasStepBar } from '@/features/workspace/steps';
import {
  applyFilter,
  applyFlag,
  applyStopped,
  canvasSourceOf,
  countFilters,
  type FlagView,
  flagOptions,
  flagsOf,
  joinRows,
  needsCheck,
  type StopView,
  type StripItem,
  stopOptions,
  type VariantView,
} from '@/features/workspace/strip';
import { useCloseRemovedStep } from '@/features/workspace/useCloseRemovedStep';
import { useStepWorkspace } from '@/features/workspace/useStepWorkspace';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * One stage of a book as a workspace: the strip of pages, the open page on the canvas, and the panel of the stage.
 *
 * The open page, the layout, the filter and the stage are the search params and the path of the route, so every view
 * is a link and survives a reload. The pages come from the manifest, which the viewer shares, and where each stands in
 * the stage comes from the rows of the stage, which the events of the book keep current. A stage with nothing of its
 * own yet shows the frame of the panel, and the pages as the last stage that has an image left them.
 */

const NO_PAGES: readonly PageSchema[] = [];
const NO_ROWS: readonly StagePageSchema[] = [];
const NOTHING_SELECTED: SelectionState = { selected: new Set(), anchorId: null };
const NO_SCANS: readonly ScanSchema[] = [];
/** The processor whose step shows the grid before the reader has chosen. */
const DESKEW_KEY = 'geometry.deskew';

/** The place of the open page in the book as the toolbar writes it, such as `p. 14 · 18 of 126`. */
function captionOf(shown: readonly StripItem[], count: number): string {
  return shown
    .map(({ page }) => MESSAGES.viewer.caption(page.label, page.position + 1, count))
    .join('  |  ');
}

/** The sentence of a page that asks for a look, or null for a page that does not. */
function plateOf(item: StripItem): string | null {
  const { row } = item;
  if (row === undefined || !needsCheck(item)) {
    return null;
  }
  if (row.status === 'failed') {
    return MESSAGES.stages.pageStatus.failed;
  }
  return row.review === null
    ? MESSAGES.stages.pageStatus.stale
    : MESSAGES.stages.review[row.review];
}

export function StageScreen({
  projectId,
  stage,
  search,
  stepId,
  onSearchChange,
  onStepChange,
}: {
  projectId: string;
  stage: Stage;
  search: StageSearch;
  /** The identifier of the step the address names, or undefined when no step is open. */
  stepId?: string;
  /** Merge changes into the search params of the route; an undefined value takes the param out. */
  onSearchChange: (changes: Partial<StageSearch>) => void;
  /** Open a step of the stage, which is a move to its address, or close the open one with undefined. */
  onStepChange: (stepId: string | undefined) => void;
}): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const manifest = useManifest(projectId);
  const rows = useStageRows(projectId, stage);
  // The rows at the open step say which pages have a bad result of that step, so they stand for the rows of the stage once
  // read; the workspace of the step reads the same query
  const stepRows = useStepRows(projectId, stage, hasStepBar(stage) ? stepId : undefined);
  const summaries = useStageSummaries(projectId);
  const [picked, setPicked] = useState({ stage, state: NOTHING_SELECTED });
  const canvas = useRef<PageCanvasHandle>(null);

  const pages = manifest.data ?? NO_PAGES;
  // A selection belongs to one stage, and to pages that are still in the book
  const stored = picked.stage === stage ? picked.state : NOTHING_SELECTED;
  const selection = useMemo(
    () => ({ ...stored, selected: pruneSelection(stored.selected, pages) }),
    [stored, pages],
  );
  // Only the Split stage looks at how wide a scan is
  const scans = useAllScans(projectId, stage === 'page-split');
  const items = useMemo(
    () => joinRows(pages, stepRows.data ?? rows.data ?? NO_ROWS, wideScanIds(scans.data ?? [])),
    [pages, stepRows.data, rows.data, scans.data],
  );
  const counts = useMemo(() => countFilters(items), [items]);
  const filter = search.filter ?? PageFilter.All;
  const listed = useMemo(() => applyFilter(items, filter), [items, filter]);
  const mode = search.view ?? ViewMode.Page;
  const spread = mode === ViewMode.Spread;
  const grid = mode === ViewMode.Grid;

  const count = items.length;
  const foundIndex =
    search.page === undefined ? -1 : items.findIndex((i) => i.page.id === search.page);
  const currentIndex = Math.max(foundIndex, 0);
  const unknownPage = manifest.data !== undefined && search.page !== undefined && foundIndex < 0;
  const indexes = viewIndexes(currentIndex, count, spread);
  const first = indexes[0] ?? 0;
  const previous = previousViewStart(first, count, spread);
  const next = nextViewStart(first, count, spread);
  const shown = indexes.flatMap((index) => items[index] ?? []);

  const stagePages = (list: readonly (StripItem | undefined)[]): StagePage[] =>
    list.flatMap((item) =>
      item === undefined ? [] : [{ id: item.page.id, infoUrl: canvasSourceOf(item) }],
    );
  const view = stagePages(shown);
  const around = [next, previous].flatMap((start) =>
    start === null ? [] : [stagePages(viewIndexes(start, count, spread).map((i) => items[i]))],
  );

  // A stage built from processors adds its recipe, its preview and its before-and-after compare to the frame
  const compareChoice = search.compare ?? CompareMode.Off;
  const currentItem = items[currentIndex];
  const processing = useProcessing(
    projectId,
    stage,
    currentItem,
    () => {
      // A preview is drawn in the half after of the compare, so asking for one turns the compare on
      if (compareChoice === CompareMode.Off) {
        onSearchChange({ compare: CompareMode.Swipe });
      }
    },
    hasStepBar(stage) ? stepId : undefined,
  );
  const workspace = useStepWorkspace(
    processing,
    hasStepBar(stage) ? stepId : undefined,
    currentItem,
  );
  const openStep = workspace.open;
  // A step that was removed from the recipe leaves the address, so no workspace stays open for a step that is gone
  useCloseRemovedStep(
    hasStepBar(stage) ? stepId : undefined,
    openStep,
    processing.ready && processing.recipe !== undefined,
    () => onStepChange(undefined),
  );
  const stageRows = useMemo(
    () => items.flatMap((item) => (item.row === undefined ? [] : [item.row])),
    [items],
  );
  // The variants of the stage mark its pages and narrow the list to one of them; the choice belongs to one stage
  const [variantPick, setVariantPick] = useState<{ stage: Stage; id: string | null }>({
    stage,
    id: null,
  });
  const variantOptions = useMemo(
    () => optionsOf(processing.recipes, items),
    [processing.recipes, items],
  );
  const variantId =
    variantPick.stage === stage && variantOptions.some((option) => option.id === variantPick.id)
      ? variantPick.id
      : null;
  const variants: VariantView | undefined =
    variantOptions.length === 0
      ? undefined
      : {
          markOf: (item) => markOf(processing.recipes, item),
          options: variantOptions,
          selected: variantId,
          onSelect: (id) => setVariantPick({ stage, id }),
        };
  // The steps a run stopped at narrow the list too; the choice belongs to one stage, like the variant
  const [stopPick, setStopPick] = useState<{ stage: Stage; step: number | null }>({
    stage,
    step: null,
  });
  const stopOptionList = useMemo(() => stopOptions(items), [items]);
  const stopStep =
    stopPick.stage === stage && stopOptionList.some((option) => option.step === stopPick.step)
      ? stopPick.step
      : null;
  const stopped: StopView | undefined =
    stopOptionList.length === 0
      ? undefined
      : {
          options: stopOptionList,
          selected: stopStep,
          onSelect: (step) => setStopPick({ stage, step }),
        };
  // The flags the server put on the pages at the open step narrow the list too; the choice belongs to one step
  const [flagPick, setFlagPick] = useState<{ step: string | undefined; flag: StepFlag | null }>({
    step: undefined,
    flag: null,
  });
  const flags = useMemo(
    () => (workspace.rows === null ? null : flagsOf(workspace.rows)),
    [workspace.rows],
  );
  const flag = flagPick.step === openStep?.stepId ? flagPick.flag : null;
  const flagged: FlagView | undefined =
    flags === null
      ? undefined
      : {
          options: flagOptions(flags),
          selected: flag,
          onSelect: (value) => setFlagPick({ step: openStep?.stepId, flag: value }),
        };
  const filtered = useMemo(() => {
    const narrowed = applyStopped(applyVariant(listed, variantId), stopStep);
    return flags === null ? narrowed : applyFlag(narrowed, flags, flag);
  }, [listed, variantId, stopStep, flags, flag]);
  const reasons = useMemo(
    () => reasonWithStep(processing.recipes, processing.catalogue),
    [processing.recipes, processing.catalogue],
  );
  const shownStep = useShownStep(processing, currentItem);
  const earlierRows = useEarlierRows(projectId, stage, processing.available);
  const beforeSource =
    currentItem === undefined
      ? null
      : beforeSourceOf(currentItem.page, currentItem.row, earlierRows);
  // The page editor of the stage, when a processor of it has one, draws over the canvas and in the panel
  const editor = useEditorSession({
    processing,
    current: currentItem,
    items,
    scans: scans.data ?? NO_SCANS,
    before: beforeSource,
    focusStepId: openStep?.stepId,
    serverFigure: workspace.page?.state ?? null,
  });

  // The grid over the page of the steps of Geometry, which the Deskew step shows until the reader chooses; it is not the
  // grid of pages, which is a way to lay out the strip
  const hasLevelGrid = stage === 'geometry';
  const [levelGridOn, toggleLevelGrid] = useGrid(projectId, openStep?.processorKey === DESKEW_KEY);
  useGridKey(toggleLevelGrid, hasLevelGrid && !grid);

  // The grid shows no open page, so the page keys and the strip have nothing to turn there
  const openPage = (pageId: string | undefined): void => {
    if (pageId !== undefined && !grid) {
      onSearchChange({ page: pageId });
    }
  };
  const openIndex = (index: number): void => {
    const clamped = Math.min(Math.max(index, 0), Math.max(count - 1, 0));
    openPage(items[viewStart(clamped, spread)]?.page.id);
  };
  const openStart = (start: number | null): void => {
    if (start !== null) {
      openIndex(start);
    }
  };

  useViewerKeys({
    previous: () => openStart(previous),
    next: () => openStart(next),
    first: () => openIndex(0),
    last: () => openStart(lastViewStart(count, spread)),
  });

  const select = (pageId: string, modifiers: ClickModifiers): void =>
    setPicked({
      stage,
      state: selectionAfterClick(
        filtered.map((item) => item.page),
        selection,
        pageId,
        modifiers,
      ),
    });

  if (manifest.isError) {
    return <ErrorAlert message={describeError(manifest.error)} />;
  }
  if (project.isError) {
    return <ErrorAlert message={describeError(project.error)} />;
  }
  if (manifest.data === undefined) {
    return <p className="p-4 text-sm text-muted-foreground">{MESSAGES.viewer.loading}</p>;
  }

  const available = summaries.data?.find((entry) => entry.stage === stage)?.available ?? true;
  const switchView = (value: ViewMode): void =>
    onSearchChange({
      view: value === ViewMode.Page ? undefined : value,
      page: items[currentIndex]?.page.id,
    });

  const header = shown.map((item) => {
    const plate = plateOf(item);
    return (
      <div key={item.page.id} className="flex items-center gap-2">
        <Badge variant="secondary">
          {MESSAGES.workspace.canvas.chip(item.page.label, MESSAGES.pages.kinds[item.page.kind])}
        </Badge>
        {item.page.included ? null : (
          <Badge variant="outline">{MESSAGES.workspace.strip.leftOut}</Badge>
        )}
        {plate === null ? null : (
          <Badge
            variant="outline"
            className={cn(
              item.row?.status === 'failed'
                ? 'border-status-failed text-status-failed'
                : 'border-status-attention text-status-attention',
            )}
          >
            <TriangleAlertIcon />
            {plate}
          </Badge>
        )}
      </div>
    );
  });

  // The compare draws the picture before beside the picture after, which is the preview while one is on
  const processed = processing.available;
  // While an editor is open the canvas shows the one picture it lies on, so nothing is compared. The editor of an open
  // step is always open, so it gives way to the compare once the reader asks for one, and comes back when it is off
  const editorOpen = editor?.active === true;
  const previewShown =
    processing.preview.on && processing.preview.shown?.page_id === currentItem?.page.id
      ? processing.preview.shown
      : null;
  const resultUrl = currentItem === undefined ? null : canvasSourceOf(currentItem);
  // A step the reader chose is drawn from its own version, which has a pyramid only when it is the current one
  const stepSource = sourceOfResult(shownStep.version);
  // An open step shows what it reads on the canvas, and what it made once the picture after is compared with it. A page
  // that has not come as far as the step reads the page as the stage would
  const stepInput =
    openStep === null ? null : (sourceOfResult(workspace.page?.input_version) ?? beforeSource);
  const stepOutput =
    openStep === null
      ? null
      : (sourceOfPreview(previewShown) ?? sourceOfResult(workspace.page?.version));
  const afterSource: ImageSource | null =
    openStep === null
      ? (sourceOfPreview(previewShown) ??
        stepSource ??
        (resultUrl === null ? null : { kind: SourceKind.Iiif, url: resultUrl }))
      : stepOutput;
  const beforePicture = openStep === null ? beforeSource : stepInput;
  const compareBlocked = spread
    ? MESSAGES.processing.compare.spreadOnly
    : editorOpen && editor?.focused !== true
      ? MESSAGES.editors.compareOff
      : beforePicture === null || (openStep !== null && stepOutput === null)
        ? MESSAGES.processing.compare.none
        : null;
  const compareMode = compareBlocked === null ? compareChoice : CompareMode.Off;
  const editing =
    editor?.active && (!editor.focused || compareMode === CompareMode.Off) ? editor : null;
  const stepLabels = MESSAGES.workspace.steps.canvas;
  const stageName = MESSAGES.stages.names[stage];
  const stageBeforeThis = stageBefore(stage);
  const { preview } = processing;
  const previewNotice = !preview.on
    ? null
    : preview.error !== null
      ? {
          text:
            preview.error === ''
              ? MESSAGES.processing.preview.failed
              : `${MESSAGES.processing.preview.failed} ${preview.error}`,
          working: false,
        }
      : preview.waiting
        ? { text: MESSAGES.processing.preview.busy, working: true }
        : preview.working
          ? { text: MESSAGES.processing.preview.working, working: true }
          : null;

  const canvasArea =
    count === 0 ? (
      <p className="p-4 text-sm text-muted-foreground">{MESSAGES.workspace.canvas.empty}</p>
    ) : (
      <div className="relative size-full">
        {processed && !spread ? (
          <CompareCanvas
            pairs={
              editing !== null
                ? { before: null, after: editing.picture }
                : openStep === null
                  ? { before: beforeSource, after: afterSource }
                  : {
                      before: stepInput,
                      after: compareMode === CompareMode.Off ? stepInput : stepOutput,
                    }
            }
            mode={compareMode}
            beforeLabel={
              openStep === null
                ? MESSAGES.processing.compare.before(
                    stageBeforeThis === null ? '' : MESSAGES.stages.names[stageBeforeThis],
                  )
                : stepLabels.before(openStep.number)
            }
            afterLabel={
              previewShown !== null
                ? MESSAGES.processing.preview.after(stageName)
                : openStep !== null
                  ? compareMode === CompareMode.Off
                    ? stepLabels.input(openStep.number, openStep.title)
                    : stepLabels.after(openStep.number, openStep.title)
                  : stepSource !== null && shownStep.index !== null
                    ? MESSAGES.processing.compare.afterStep(stageName, shownStep.index + 1)
                    : MESSAGES.processing.compare.after(stageName)
            }
            notice={previewNotice}
            pageIds={shown.map((item) => item.page.id)}
            handle={canvas}
            overlay={editing === null ? undefined : (scene) => editing.renderCanvas(scene)}
            roomShare={editing === null ? 0 : EDITOR_ROOM_SHARE}
          />
        ) : (
          <PageCanvas view={view} around={around} fitMode={FitMode.Page} handle={canvas} />
        )}
        {unknownPage ? (
          <p className="absolute inset-x-0 top-3 mx-auto w-fit rounded-md bg-background/90 px-3 py-1 text-sm shadow">
            {MESSAGES.viewer.unknownPage}
          </p>
        ) : null}
        {hasLevelGrid && levelGridOn ? <GridOverlay /> : null}
        <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center">
          <div className="pointer-events-auto">
            <CanvasToolbar
              caption={captionOf(shown, count)}
              spread={spread}
              hasPrevious={previous !== null}
              hasNext={next !== null}
              onPrevious={() => openStart(previous)}
              onNext={() => openStart(next)}
              onToggleSpread={() => switchView(spread ? ViewMode.Page : ViewMode.Spread)}
              onFit={() => canvas.current?.fit(FitMode.Page)}
              onZoomIn={() => canvas.current?.zoomIn()}
              onZoomOut={() => canvas.current?.zoomOut()}
              grid={hasLevelGrid ? { on: levelGridOn, onToggle: toggleLevelGrid } : undefined}
              compare={
                processed
                  ? {
                      mode: compareMode,
                      onChange: (value) =>
                        onSearchChange({ compare: value === CompareMode.Off ? undefined : value }),
                      unavailable: compareBlocked,
                    }
                  : undefined
              }
            />
          </div>
        </div>
      </div>
    );

  return (
    <div className="flex size-full flex-col" data-testid="stage-screen" data-stage={stage}>
      {rows.isError ? <ErrorAlert message={describeError(rows.error)} /> : null}
      <div className="min-h-0 flex-1">
        <StageWorkspace
          strip={
            grid ? null : (
              <StageStrip
                items={filtered}
                total={count}
                counts={counts}
                filter={filter}
                currentId={items[currentIndex]?.page.id}
                onFilter={(value) => onSearchChange({ filter: value })}
                onOpen={openPage}
                onGrid={() => switchView(ViewMode.Grid)}
                reasonOf={processed ? reasons : undefined}
                withWide={stage === 'page-split'}
                variants={variants}
                stopped={stopped}
                flagged={flagged}
              />
            )
          }
          canvasHeader={grid ? null : header}
          stepBar={
            grid || !hasStepBar(stage) || workspace.steps.length === 0 ? null : (
              <StepBar
                steps={workspace.steps}
                openId={openStep?.stepId}
                states={workspace.states}
                recipes={processing.recipes}
                recipeId={processing.recipe?.id}
                onChooseRecipe={processing.chooseRecipe}
                onOpen={onStepChange}
                actions={
                  <>
                    <StepCatalogue
                      processing={processing}
                      rows={stageRows}
                      onAdded={onStepChange}
                    />
                    <StepsWindow processing={processing} rows={stageRows} />
                  </>
                }
              />
            )
          }
          canvas={
            grid ? (
              <StageGrid
                items={filtered}
                total={count}
                counts={counts}
                filter={filter}
                selected={selection.selected}
                onFilter={(value) => onSearchChange({ filter: value })}
                onSelect={select}
                onClearSelection={() => setPicked({ stage, state: NOTHING_SELECTED })}
                onOpen={(pageId) => onSearchChange({ view: undefined, page: pageId })}
                onList={() => switchView(ViewMode.Page)}
                reasonOf={processed ? reasons : undefined}
                withWide={stage === 'page-split'}
                variants={variants}
                stopped={stopped}
                flagged={flagged}
              />
            ) : processed ? (
              <div className="flex size-full flex-col">
                <StageBanners processing={processing} items={items} />
                <div className="min-h-0 flex-1">{canvasArea}</div>
              </div>
            ) : (
              canvasArea
            )
          }
          panel={
            processed ? (
              <ProcessingPanel
                processing={processing}
                items={items}
                current={currentItem}
                selected={selection.selected}
                editor={editor}
                step={
                  openStep === null
                    ? undefined
                    : {
                        workspace,
                        step: openStep,
                        pageLabel: currentItem?.page.label ?? '',
                        onOpen: onStepChange,
                      }
                }
              />
            ) : (
              <StagePanel stage={stage} available={available} />
            )
          }
        />
      </div>
    </div>
  );
}
