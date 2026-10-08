import {
  ALIGN_HORIZONTAL,
  ALIGN_VERTICAL,
  choiceOf,
  type HorizontalChoice,
  horizontalChoices,
  MARGINS_BY,
  VERTICAL_CHOICES,
  type VerticalChoice,
} from '@/features/editors/alignment';
import { useStepSettings } from '@/features/editors/stepSettings';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
import { MESSAGES } from '@/shared/messages';

/**
 * The part of the Margins editor in the panel: the sentence that says how the box and the border are moved, and the
 * alignment of the box on the page, which is a setting of the open page. The horizontal choices follow how the side margins
 * are told apart, the gutter side and the outer side or left and right.
 */

const labels = MESSAGES.editors.margins.alignment;

export function MarginsPanel(): React.JSX.Element {
  const settings = useStepSettings();
  const values = settings?.values ?? {};
  const vertical = choiceOf<VerticalChoice>(values[ALIGN_VERTICAL], VERTICAL_CHOICES, 'top');
  const horizontalOptions = horizontalChoices(values[MARGINS_BY]);
  const horizontal = choiceOf<HorizontalChoice>(
    values[ALIGN_HORIZONTAL],
    horizontalOptions,
    'center',
  );
  return (
    <div className="grid basis-full gap-3" data-testid="margins-panel">
      <div data-testid="align-vertical">
        <SegmentedRadio
          legend={labels.vertical}
          disabled={settings === null || settings.busy}
          value={vertical}
          options={VERTICAL_CHOICES.map((choice) => ({ value: choice, label: labels[choice] }))}
          onChange={(choice) => settings?.set(ALIGN_VERTICAL, choice)}
        />
      </div>
      <div data-testid="align-horizontal">
        <SegmentedRadio
          legend={labels.horizontal}
          disabled={settings === null || settings.busy}
          value={horizontal}
          options={horizontalOptions.map((choice) => ({ value: choice, label: labels[choice] }))}
          onChange={(choice) => settings?.set(ALIGN_HORIZONTAL, choice)}
        />
      </div>
    </div>
  );
}
