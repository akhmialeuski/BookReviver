import type { SignInProvider } from '@/api';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * One button per provider the server offers, and nothing at all when it offers none.
 *
 * It only draws the list it is given: asking the server and leaving for the provider are the job of
 * `SocialSignIn`, so which buttons appear for which list is checked without a browser.
 */

export function ProviderButtons({
  providers,
  disabled,
  onChoose,
}: {
  providers: readonly SignInProvider[];
  disabled: boolean;
  onChoose: (provider: SignInProvider) => void;
}): React.JSX.Element | null {
  if (providers.length === 0) {
    return null;
  }
  return (
    <div className="grid gap-3">
      <p className="text-center text-xs text-muted-foreground">{MESSAGES.auth.providers.divider}</p>
      {providers.map((provider) => (
        <Button
          key={provider.name}
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={() => onChoose(provider)}
        >
          {MESSAGES.auth.providers.continueWith(provider.label)}
        </Button>
      ))}
    </div>
  );
}
