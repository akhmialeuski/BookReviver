import { PlusIcon, XIcon } from 'lucide-react';
import { useState } from 'react';
import type { RecipeRuleSchema, RecipeSchema, RuleCondition } from '@/api';
import { useCreateRule, useDeleteRule, useRetargetRule } from '@/features/processing/queries';
import { ruleFor, rulesOf } from '@/features/processing/variants';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * What a variant is used for: the pages it made, and the rules that send pages to it whenever the stage runs.
 *
 * A stage has one rule for each condition, so a condition that already has a rule moves it to this variant instead of
 * adding a second one that could never be reached. The condition on a group asks for the name of the group first.
 */

const labels = MESSAGES.processing.usedFor;

/** The conditions in the order the menu lists them. */
const CONDITIONS: readonly RuleCondition[] = [
  'plates',
  'covers',
  'blanks',
  'odd',
  'even',
  'group',
  'illustrated',
];

/** Write what a rule asks of a page. */
export function describeRule(rule: Pick<RecipeRuleSchema, 'condition' | 'group_label'>): string {
  return labels.rule(labels.conditions[rule.condition], rule.group_label);
}

export function UsedFor({
  projectId,
  recipe,
  recipes,
  rules,
  pages,
}: {
  projectId: string;
  /** The variant shown. */
  recipe: RecipeSchema;
  /** Every recipe of the stage, which names the variant a rule is moved from. */
  recipes: readonly RecipeSchema[];
  /** Every rule of the stage in the order they are tried. */
  rules: readonly RecipeRuleSchema[];
  /** How many pages the variant made. */
  pages: number;
}): React.JSX.Element {
  const { stage } = recipe;
  const create = useCreateRule(projectId, stage);
  const retarget = useRetargetRule(projectId, stage);
  const remove = useDeleteRule(projectId, stage);
  const [naming, setNaming] = useState(false);
  const [group, setGroup] = useState('');
  const own = rulesOf(rules, recipe.id);
  const error = create.error ?? retarget.error ?? remove.error;
  const busy = create.isPending || retarget.isPending || remove.isPending;

  const send = (condition: RuleCondition, groupLabel: string): void => {
    const existing = ruleFor(rules, condition, groupLabel);
    if (existing === undefined) {
      create.mutate({
        path: { project_id: projectId, stage },
        body: { condition, group_label: groupLabel, recipe_id: recipe.id },
      });
    } else if (existing.recipe_id !== recipe.id) {
      retarget.mutate({
        path: { project_id: projectId, stage, rule_id: existing.id },
        body: { recipe_id: recipe.id },
      });
    }
  };

  return (
    <section className="grid gap-2" aria-label={labels.title} data-testid="used-for">
      <div className="flex min-h-6 items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.title}
        </h3>
        <span className="text-xs text-muted-foreground" data-testid="used-for-pages">
          {labels.pages(pages)}
        </span>
      </div>
      {own.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="used-for-empty">
          {labels.empty}
        </p>
      ) : (
        <ul className="grid gap-1" data-testid="rules">
          {own.map((rule) => (
            <li
              key={rule.id}
              className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm"
              data-testid="rule"
            >
              <span className="flex-1 break-words">{describeRule(rule)}</span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={labels.remove(describeRule(rule))}
                disabled={busy}
                data-testid="rule-remove"
                onClick={() =>
                  remove.mutate({ path: { project_id: projectId, stage, rule_id: rule.id } })
                }
              >
                <XIcon />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {naming ? (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const name = group.trim();
            if (name !== '') {
              send('group', name);
              setGroup('');
              setNaming(false);
            }
          }}
        >
          <input
            aria-label={labels.groupLabel}
            placeholder={labels.groupLabel}
            value={group}
            data-testid="rule-group-name"
            className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            onChange={(event) => setGroup(event.target.value)}
          />
          <Button type="submit" size="sm" disabled={busy || group.trim() === ''}>
            {labels.groupAdd}
          </Button>
        </form>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            title={labels.addHint}
            disabled={busy}
            data-testid="rule-add"
          >
            <PlusIcon />
            {labels.add}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {CONDITIONS.map((condition) => {
            const taken = condition === 'group' ? undefined : ruleFor(rules, condition);
            const here = taken?.recipe_id === recipe.id;
            const other = recipes.find((entry) => entry.id === taken?.recipe_id);
            return (
              <DropdownMenuItem
                key={condition}
                disabled={here}
                data-testid={`rule-add-${condition}`}
                title={
                  condition === 'illustrated'
                    ? labels.notYet
                    : other === undefined || here
                      ? undefined
                      : labels.moves(labels.conditions[condition], other.name)
                }
                onSelect={() => (condition === 'group' ? setNaming(true) : send(condition, ''))}
              >
                {labels.conditions[condition]}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </section>
  );
}
