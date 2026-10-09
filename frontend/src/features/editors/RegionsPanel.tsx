import { MinusIcon, PlusIcon, Trash2Icon } from 'lucide-react';
import { addZone, newZone, removeZone } from '@/features/editors/regions';
import { type RegionsShape, ZoneMode } from '@/features/editors/shapes';
import type { PanelProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The part of the picture zone editor in the panel: the two buttons that put a new zone in the middle of the page, and
 * the list of the zones the reader drew, each with a button that deletes it.
 */

const labels = MESSAGES.editors.regions;

export function RegionsPanel({
  shape,
  disabled,
  size,
  onCommit,
}: PanelProps<RegionsShape>): React.JSX.Element {
  const add = (mode: ZoneMode): void => {
    if (size !== null) {
      onCommit(addZone(shape, newZone(mode, size)));
    }
  };
  const entries = shape.zones.map((zone, place) => ({
    id: `zone-${place}`,
    place,
    name: labels.zoneName(zone.mode, place + 1),
  }));
  return (
    <div className="grid basis-full gap-2" data-testid="regions-panel">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || size === null}
          data-testid="regions-add"
          onClick={() => add(ZoneMode.Add)}
        >
          <PlusIcon />
          {labels.addZone}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || size === null}
          data-testid="regions-remove"
          onClick={() => add(ZoneMode.Remove)}
        >
          <MinusIcon />
          {labels.removeZone}
        </Button>
      </div>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.empty}</p>
      ) : (
        <ul className="grid gap-1" aria-label={labels.zones} data-testid="regions-list">
          {entries.map(({ id, place, name }) => (
            <li key={id} className="flex items-center justify-between gap-2 text-sm">
              <span>{name}</span>
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={disabled}
                aria-label={labels.deleteZone(name)}
                data-testid="regions-delete"
                onClick={() => onCommit(removeZone(shape, place))}
              >
                <Trash2Icon />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
