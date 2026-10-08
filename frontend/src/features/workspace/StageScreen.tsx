import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { PageSchema, RecipeKind, ScanSchema, Stage, StagePageSchema, StepFlag } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { EDITOR_ROOM_SHARE } from '@/features/editors/scene';
import { useEditorSession } from '@/features/editors/useEditorSession';
import { useManifest } from '@/features/pages/manifest';
import { pruneSelection } from '@/features/pages/selection';
import { CompareCanvas } from '@/features/processing/CompareCanvas';
import { ContentTypeMenu } from '@/features/processing/ContentTypeMenu';
import { comparePairOf, placementOf, sameImage } from '@/features/processing/compare';
import { ProcessingPanel } from '@/features/processing/ProcessingPanel';
import { useAllScans } from '@/features/processing/queries';
import { RecipePicker } from '@/features/processing/RecipePicker';
import { reasonWithStep } from '@/features/processing/reasons';
import { StageBanners } from '@/features/processing/StageBanners';
import { wideScanIds } from '@/features/processing/split';
import { useProcessing } from '@/features/processing/useProcessing';
import { stageBefore } from '@/features/stages/stages';
import { PageCanvas, type PageCanvasHandle } from '@/features/viewer/PageCanvas';
import { FitMode, type StagePage } from '@/features/viewer/stage';
import { usePageNavigation } from '@/features/viewer/usePageNavigation';
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
import { barStepsOf, defaultStepOf, hasStepBar } from '@/features/workspace/steps';
import {
  applyFilter,
  applyFlag,
  applyKind,
  applyStopped,
  canvasSourceOf,
  countFilters,
  type FlagView,
  flagOptions,
  flagsOf,
  joinRows,
  type KindView,
  kindOptionsOf,
  pictureOf,
  type StopView,
  type StripItem,
  stopOptions,
  stripRowsOf,
} from '@/features/workspace/strip';
import { useCloseRemovedStep } from '@/features/workspace/useCloseRemovedStep';
import { useStepWorkspace } from '@/features/workspace/useStepWorkspace';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
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

export function StageScreen({
  projectId,
  stage,
  search,
  stepId,
  onSearchChange,
  onStepChange,
  onDefaultStep,
}: {
  projectId: string;
  stage: Stage;
  search: StageSearch;
  /** The identifier of the step the address names, or undefined when it names none. */
  stepId?: string;
  /** Merge changes into the search params of the route; an undefined value takes the param out. */
  onSearchChange: (changes: Partial<StageSearch>) => void;
  /** Open a step of the stage, which is a move to its address. */
  onStepChange: (stepId: string) => void;
  /** Put the step a stage with a bar opens on in place of an address that names none, so Back does not return to it. */
  onDefaultStep: (stepId: string) => void;
}): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const manifest = useManifest(projectId);
  const rows = useStageRows(projectId, stage);
  const barStage = hasStepBar(stage);
  // The rows at the open step carry the picture of the step and the marks of its results, so on a stage with a bar they are
  // the rows of the strip, the canvas and the counts; the workspace of the step reads the same query
  const stepRows = useStepRows(projectId, stage, barStage ? stepId : undefined);
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
    () =>
      joinRows(
        pages,
        stripRowsOf(barStage, stepRows.data, rows.data) ?? NO_ROWS,
        wideScanIds(scans.data ?? []),
      ),
    [pages, barStage, stepRows.data, rows.data, scans.data],
  );
  const counts = useMemo(() => countFilters(items), [items]);
  const filter = search.filter ?? PageFilter.All;
  const listed = useMemo(() => applyFilter(items, filter), [items, filter]);
  const mode = search.view ?? ViewMode.Page;
  const spread = mode === ViewMode.Spread;
  const grid = mode === ViewMode.Grid;

  const count = items.length;
  // The grid shows no open page, so the page keys and the strip have nothing to turn there
  const openPage = (pageId: string): void => {
    if (!grid) {
      onSearchChange({ page: pageId });
    }
  };
  const navigation = usePageNavigation(
    items,
    (item) => item.page.id,
    search.page,
    spread,
    openPage,
  );
  const { currentIndex, shown } = navigation;

  const toStagePage = (item: StripItem): StagePage => ({
    id: item.page.id,
    infoUrl: canvasSourceOf(item),
  });
  const view = shown.map(toStagePage);
  const around = navigation.around.map((group) => group.map(toStagePage));

  // A stage built from processors adds its recipe and its before-and-after compare to the frame
  const compareChoice = search.compare ?? CompareMode.Off;
  const currentItem = items[currentIndex];
  const processing = useProcessing(
    projectId,
    stage,
    barStage ? stepId : undefined,
    currentItem?.row?.kind,
  );
  const workspace = useStepWorkspace(processing, barStage ? stepId : undefined, currentItem);
  const openStep = workspace.open;
  // A stage with a bar always has a step open: the one a run has brought the pages of the recipe furthest to
  const defaultStep = useMemo(
    () =>
      barStage && processing.recipe !== undefined && rows.data !== undefined
        ? defaultStepOf(workspace.steps, rows.data, processing.recipe.id)
        : null,
    [barStage, processing.recipe, rows.data, workspace.steps],
  );
  const defaultStepId = defaultStep?.stepId;
  useEffect(() => {
    if (barStage && stepId === undefined && defaultStepId !== undefined) {
      onDefaultStep(defaultStepId);
    }
  }, [barStage, stepId, defaultStepId, onDefaultStep]);
  // A step that left the recipe shown leaves the address, so no workspace stays open for a step that is gone. A page of
  // another kind shows another recipe, and the step of the same processor in it is opened, so the reader stays on the
  // step they were at; a step that was removed gives way to the default step
  useCloseRemovedStep(
    barStage ? stepId : undefined,
    openStep,
    processing.ready && processing.recipe !== undefined,
    () => {
      const processorKey = processing.recipes
        .flatMap((entry) => entry.steps)
        .find((step) => step.step_id === stepId)?.processor_key;
      const next =
        workspace.steps.find((step) => step.processorKey === processorKey)?.stepId ?? defaultStepId;
      if (next !== undefined) {
        onDefaultStep(next);
      }
    },
  );
  const stageRows = useMemo(
    () => items.flatMap((item) => (item.row === undefined ? [] : [item.row])),
    [items],
  );
  // The kinds of page narrow the list to one of them; the choice belongs to one stage
  const [kindPick, setKindPick] = useState<{ stage: Stage; kind: RecipeKind | null }>({
    stage,
    kind: null,
  });
  const kindOptions = useMemo(
    () => kindOptionsOf(summaries.data?.find((entry) => entry.stage === stage)),
    [summaries.data, stage],
  );
  const pickedKind =
    kindPick.stage === stage && kindOptions.some((option) => option.kind === kindPick.kind)
      ? kindPick.kind
      : null;
  const kinds: KindView | undefined =
    kindOptions.length === 0
      ? undefined
      : {
          options: kindOptions,
          selected: pickedKind,
          onSelect: (kind) => setKindPick({ stage, kind }),
        };
  // The steps a run stopped at narrow the list too; the choice belongs to one stage, like the kind of page
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
          titleOf: (index) =>
            barStepsOf(processing.recipe, processing.catalogue)[index]?.title ?? '',
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
    const narrowed = applyStopped(applyKind(listed, pickedKind), stopStep);
    return flags === null ? narrowed : applyFlag(narrowed, flags, flag);
  }, [listed, pickedKind, stopStep, flags, flag]);
  const reasons = useMemo(
    () => reasonWithStep(processing.recipes, processing.catalogue),
    [processing.recipes, processing.catalogue],
  );
  // The picture before is the one the strip shows, and the picture after is what the open step made, or the result of the
  // stage when no step is open
  const pair = comparePairOf(
    currentItem === undefined ? null : pictureOf(currentItem),
    currentItem?.row,
  );
  // The page editor of the stage, when a processor of it has one, draws over the canvas and in the panel
  const editor = useEditorSession({
    processing,
    current: currentItem,
    items,
    scans: scans.data ?? NO_SCANS,
    before: pair.before,
    focusStepId: openStep?.stepId,
    serverFigure: workspace.page?.state ?? null,
  });

  // The grid over the page of the steps of Geometry, which the Deskew step shows until the reader chooses; it is not the
  // grid of pages, which is a way to lay out the strip
  const hasLevelGrid = stage === 'geometry';
  const [levelGridOn, toggleLevelGrid] = useGrid(projectId, openStep?.processorKey === DESKEW_KEY);
  useGridKey(toggleLevelGrid, hasLevelGrid && !grid);

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

  // The compare draws the picture before beside the picture after, which is the result of the open step or of the stage
  const processed = processing.available;
  // While an editor is open the canvas shows the one picture it lies on, so nothing is compared. The editor of an open
  // step is always open, so it gives way to the compare once the reader asks for one, and comes back when it is off
  const editorOpen = editor?.active === true;
  const compareBlocked = spread
    ? MESSAGES.processing.compare.spreadOnly
    : editorOpen && editor?.focused !== true
      ? MESSAGES.editors.compareOff
      : pair.before === null || pair.after === null || sameImage(pair.before, pair.after)
        ? MESSAGES.processing.compare.none
        : null;
  const compareMode = compareBlocked === null ? compareChoice : CompareMode.Off;
  const editing =
    editor?.active && (!editor.focused || compareMode === CompareMode.Off) ? editor : null;
  // A step that moves and scales its input, such as the margins, makes a picture that stands inside the other, or the other
  // way round
  const placement =
    editing !== null || openStep === null || compareMode === CompareMode.Off
      ? null
      : placementOf(workspace.page?.version, workspace.page?.input_version);
  const stepLabels = MESSAGES.workspace.steps.canvas;
  const stageName = MESSAGES.stages.names[stage];
  const stageBeforeThis = stageBefore(stage);
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
                : {
                    before: pair.before,
                    // With the compare off the canvas draws one picture: the picture of the strip, which is the picture before,
                    // while a step is open, and the result of the stage when none is, or the picture before until it has one
                    after:
                      compareMode !== CompareMode.Off
                        ? pair.after
                        : openStep === null
                          ? (pair.after ?? pair.before)
                          : pair.before,
                  }
            }
            mode={compareMode}
            beforeLabel={
              openStep === null
                ? MESSAGES.processing.compare.before(
                    stageBeforeThis === null ? '' : MESSAGES.stages.names[stageBeforeThis],
                  )
                : stepLabels.before(openStep.title)
            }
            afterLabel={
              openStep !== null
                ? compareMode === CompareMode.Off
                  ? stepLabels.before(openStep.title)
                  : stepLabels.after(openStep.title)
                : MESSAGES.processing.compare.after(stageName)
            }
            pageIds={shown.map((item) => item.page.id)}
            handle={canvas}
            overlay={editing === null ? undefined : (scene) => editing.renderCanvas(scene)}
            roomShare={editing === null ? 0 : EDITOR_ROOM_SHARE}
            reach={editing?.reach ?? null}
            placement={placement}
          />
        ) : (
          <PageCanvas view={view} around={around} fitMode={FitMode.Page} handle={canvas} />
        )}
        {navigation.unknownPage ? (
          <p className="absolute inset-x-0 top-3 mx-auto w-fit rounded-md bg-background/90 px-3 py-1 text-sm shadow">
            {MESSAGES.viewer.unknownPage}
          </p>
        ) : null}
        {hasLevelGrid && levelGridOn ? <GridOverlay /> : null}
        <div className="@container pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center">
          <div className="pointer-events-auto">
            <CanvasToolbar
              caption={captionOf(shown, count)}
              spread={spread}
              hasPrevious={navigation.hasPrevious}
              hasNext={navigation.hasNext}
              onPrevious={navigation.openPrevious}
              onNext={navigation.openNext}
              onToggleSpread={() => switchView(spread ? ViewMode.Page : ViewMode.Spread)}
              onFit={() => canvas.current?.fit(FitMode.Page)}
              onZoomIn={() => canvas.current?.zoomIn()}
              onZoomOut={() => canvas.current?.zoomOut()}
              grid={hasLevelGrid ? { on: levelGridOn, onToggle: toggleLevelGrid } : undefined}
              contentType={
                processed && stage !== 'page-split' ? (
                  <ContentTypeMenu
                    projectId={projectId}
                    items={items}
                    currentId={currentItem?.page.id}
                    selected={selection.selected}
                  />
                ) : undefined
              }
              auto={
                editor?.hasEdit === true ? { busy: editor.busy, onClick: editor.auto } : undefined
              }
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
                kinds={kinds}
                stopped={stopped}
                flagged={flagged}
              />
            )
          }
          stepBar={
            grid || !hasStepBar(stage) || workspace.steps.length === 0
              ? null
              : (toggles) => (
                  <StepBar
                    leading={toggles.strip}
                    trailing={toggles.panel}
                    steps={workspace.steps}
                    openId={openStep?.stepId}
                    states={workspace.states}
                    recipePicker={
                      <RecipePicker processing={processing} className="h-8 shrink-0 px-2" />
                    }
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
                kinds={kinds}
                stopped={stopped}
                flagged={flagged}
              />
            ) : processed ? (
              <div className="relative size-full">
                {canvasArea}
                <StageBanners processing={processing} items={items} />
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
                step={openStep === null ? undefined : { workspace, step: openStep }}
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
