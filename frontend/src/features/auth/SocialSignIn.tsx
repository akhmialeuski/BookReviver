import { useMutation } from '@tanstack/react-query';
import { ProviderButtons } from '@/features/auth/ProviderButtons';
import { requestAuthorizationUrl, useProviders } from '@/features/auth/providers';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The block of provider buttons under the sign-in and registration forms.
 *
 * A click asks the server for the provider's address, which also sets the state cookie that the callback checks
 * later, and then the browser leaves for that address. The block is absent while the list loads and when the server
 * offers no provider.
 */

export function SocialSignIn(): React.JSX.Element | null {
  const providers = useProviders();
  const start = useMutation({
    mutationFn: requestAuthorizationUrl,
    onSuccess: (url) => {
      window.location.assign(url);
    },
  });
  const going = providers.find((provider) => provider.name === start.variables);

  return (
    <>
      <ProviderButtons
        providers={providers}
        disabled={start.isPending || start.isSuccess}
        onChoose={(provider) => start.mutate(provider.name)}
      />
      {going !== undefined && start.isSuccess ? (
        <p className="text-center text-sm text-muted-foreground">
          {MESSAGES.auth.providers.redirecting(going.label)}
        </p>
      ) : null}
      {start.isError ? <ErrorAlert message={describeError(start.error)} /> : null}
    </>
  );
}
