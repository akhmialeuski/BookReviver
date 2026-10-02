import { expect, type Page, test } from '@playwright/test';
import { mailedLink } from './support/account';

/**
 * The ways into an account besides the first registration: signing in with the fake provider the end-to-end server
 * offers, the failures of that sign-in, and a forgotten password reset from the mail in the log.
 */

const PASSWORD = 'correct horse battery staple';
const NEW_PASSWORD = 'a completely different phrase';
// The authorization code of the fake provider is the address it vouches for; see tests/helpers/fake_oauth.py
const PROVIDER_CODE = 'reader@example.org';

/** Wait for the mail to the address in the server log and return the path and query of its link to the page. */
async function mailedPath(email: string, page: string): Promise<string> {
  const link = new URL(await mailedLink(email, page));
  return link.pathname + link.search;
}

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test('a reader signs in with a provider and lands on the book list', async ({ page }) => {
  await test.step('the sign-in and registration screens offer the provider', async () => {
    await page.goto('/register');
    await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Continue with Facebook' })).toHaveCount(0);
    await page.goto('/sign-in');
    await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  });

  await test.step('the button goes through the provider and back to the book list', async () => {
    await page.getByRole('button', { name: 'Continue with Google' }).click();
    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
  });
});

test('a sign-in the provider refused, or that was not started here, says why', async ({ page }) => {
  await test.step('the user declined at the provider', async () => {
    await page.goto('/auth/google/callback?error=access_denied&state=anything');
    await expect(page).toHaveURL(/\/sign-in\?oauthError=refused$/);
    await expect(page.getByText('The sign-in was cancelled')).toBeVisible();
  });

  await test.step('the state does not belong to this browser', async () => {
    await page.goto(`/auth/google/callback?code=${PROVIDER_CODE}&state=forged`);
    await expect(page).toHaveURL(/\/sign-in\?oauthError=invalid-state$/);
    await expect(page.getByText('could not be checked')).toBeVisible();
  });
});

test('a reader who forgot the password resets it from the mailed link', async ({ page }) => {
  const email = `forgetful-${Date.now()}@example.com`;

  await test.step('register and confirm the address', async () => {
    await page.goto('/register');
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByText('Check your mail')).toBeVisible();
    await page.goto(await mailedPath(email, 'verify-email'));
    await expect(page.getByText('Your address is confirmed')).toBeVisible();
  });

  await test.step('ask for a reset mail from the sign-in screen', async () => {
    await page.goto('/sign-in');
    await page.getByRole('link', { name: 'Forgot password?' }).click();
    // Both screens have an "Email address" field, so without this wait the text goes into the sign-in form that is
    // still on the screen and the reset form then opens empty. The card title is a div, not a heading.
    await expect(page.getByText('Reset your password', { exact: true })).toBeVisible();
    await page.getByLabel('Email address').fill(email);
    await page.getByRole('button', { name: 'Send the link' }).click();
    await expect(page.getByText('Check your mail')).toBeVisible();
  });

  await test.step('open the link and choose a new password', async () => {
    await page.goto(await mailedPath(email, 'reset-password'));
    await page.getByLabel('New password').fill(NEW_PASSWORD);
    await page.getByRole('button', { name: 'Save the password' }).click();
    await expect(page.getByText('Your password is changed')).toBeVisible();
  });

  await test.step('the old password no longer works and the new one does', async () => {
    await page.getByRole('link', { name: 'Go to sign in' }).click();
    await signIn(page, email, PASSWORD);
    await expect(page.getByText('The email address or the password is wrong.')).toBeVisible();
    await signIn(page, email, NEW_PASSWORD);
    await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
  });
});

test('a reset link that was not issued by the server is refused', async ({ page }) => {
  await page.goto('/reset-password?token=forged');
  await page.getByLabel('New password').fill(NEW_PASSWORD);
  await page.getByRole('button', { name: 'Save the password' }).click();
  await expect(page.getByText('not valid or has expired')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ask for a new link' })).toBeVisible();
});
