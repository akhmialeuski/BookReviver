import { createFileRoute } from '@tanstack/react-router';
import { ProfilesPage } from '@/features/profiles/ProfilesPage';
import { PageContainer } from '@/shared/ui/page-container';

/**
 * The recipe profiles of the signed-in account, `/settings/profiles`, which the account menu and the bar of the library
 * link to.
 */

export const Route = createFileRoute('/_authenticated/settings/profiles')({
  component: ProfilesRoute,
});

function ProfilesRoute(): React.JSX.Element {
  return (
    <PageContainer>
      <ProfilesPage />
    </PageContainer>
  );
}
