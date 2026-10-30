// Storage screen journeys (docs/UI.md, Storage; P09.2): figures, the guarded removal of an original
// video, cancelling it, an API refusal, a failed load and an empty list, in English and Italian.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { scanContrast } from './support/contrast';
import { REMOVABLE_PROJECT, useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    used: 'Used by projects',
    totalBefore: '39.0 MB',
    totalAfter: '23.0 MB',
    free: '20.0 GB',
    freed: '16.0 MB',
    remove: 'Remove original video',
    cancel: 'Cancel',
    removedNotice: `Original video of ${REMOVABLE_PROJECT} removed. 16.0 MB freed.`,
    packageRequired: 'Create a package of the current review first.',
    alreadyRemoved: 'The original video was removed.',
    busy: 'Wait until the project finishes its current work.',
    noVideo: 'This project has no original video yet.',
    createPackage: 'Create a package for',
    // English shows the API's own message; other languages show the translation of the code.
    refused: 'A package is required.',
    unreachable: 'Narrastep could not reach the local service.',
    tryAgain: 'Try again',
    empty: 'Projects you create will show their size here.',
  },
  {
    code: 'it',
    locale: 'it-IT',
    used: 'Usato dai progetti',
    totalBefore: '39,0 MB',
    totalAfter: '23,0 MB',
    free: '20,0 GB',
    freed: '16,0 MB',
    remove: 'Rimuovi video originale',
    cancel: 'Annulla',
    removedNotice: `Video originale di ${REMOVABLE_PROJECT} rimosso. Liberati 16,0 MB.`,
    packageRequired: 'Crea prima un pacchetto della revisione attuale.',
    alreadyRemoved: 'Il video originale è stato rimosso.',
    busy: 'Aspetta che il progetto finisca il lavoro in corso.',
    noVideo: 'Questo progetto non ha ancora un video originale.',
    createPackage: 'Crea un pacchetto per',
    refused: 'Crea prima un pacchetto della revisione attuale. Solo dopo puoi rimuovere il video',
    unreachable: 'Narrastep non riesce a raggiungere il servizio locale.',
    tryAgain: 'Riprova',
    empty: 'I progetti che crei mostreranno qui la loro dimensione.',
  },
] as const;

/** Sets up the fake API and returns the paths of every removal request sent to it. */
async function watchRemovals(context: BrowserContext): Promise<string[]> {
  await useFakeApi(context, { status: 'review' });
  const removals: string[] = [];
  context.on('request', (request) => {
    if (request.method() === 'DELETE') removals.push(new URL(request.url()).pathname);
  });
  return removals;
}

/** Opens the Storage screen and waits for its figures. */
async function openStorage(page: Page, language: (typeof LANGUAGES)[number]): Promise<void> {
  await page.goto('/storage');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  await expect(page.getByText(language.used)).toBeVisible();
}

/** The row of the project whose original video can be removed. */
function removableRow(page: Page) {
  return page.locator('.storage-row').filter({ hasText: REMOVABLE_PROJECT });
}

for (const language of LANGUAGES) {
  test.describe(`Storage in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('shows the figures and why a video cannot be removed', async ({ context, page }) => {
      await watchRemovals(context);
      await openStorage(page, language);

      await expect(page.locator('.totals')).toContainText(language.totalBefore);
      await expect(page.locator('.totals')).toContainText(language.free);
      await expect(page.locator('.storage-row')).toHaveCount(5);
      await expect(page.locator('.storage-row').first()).toContainText(REMOVABLE_PROJECT);
      // Exactly one row offers the removal; the others say why not.
      await expect(page.getByRole('button', { name: language.remove })).toHaveCount(0);
      await expect(removableRow(page).getByRole('button')).toHaveCount(1);
      for (const reason of [
        language.packageRequired,
        language.alreadyRemoved,
        language.busy,
        language.noVideo,
      ]) {
        await expect(page.locator('.storage-row').filter({ hasText: reason })).toHaveCount(1);
      }
      // Only the project in review links to the screen that creates the missing package.
      const link = page.getByRole('link', { name: new RegExp(`^${language.createPackage} `) });
      await expect(link).toHaveCount(1);
      await link.click();
      await expect(page).toHaveURL(/\/projects\/project-1\/export$/);
    });

    test('removes the video only after the confirmation and updates the figures', async ({
      context,
      page,
    }) => {
      const removals = await watchRemovals(context);
      await openStorage(page, language);
      await removableRow(page).getByRole('button').click();

      const dialog = page.getByRole('dialog');
      await expect(dialog).toContainText(REMOVABLE_PROJECT);
      await expect(dialog).toContainText(language.freed);
      expect(removals).toEqual([]);

      await dialog.getByRole('button', { name: language.remove }).click();

      await expect(dialog).toBeHidden();
      await expect(page.getByRole('status').filter({ hasText: REMOVABLE_PROJECT })).toHaveText(
        language.removedNotice,
      );
      expect(removals).toEqual(['/api/v1/projects/project-2/source-video']);
      await expect(page.locator('.totals')).toContainText(language.totalAfter);
      await expect(removableRow(page)).toContainText(language.alreadyRemoved);
      await expect(removableRow(page).getByRole('button')).toHaveCount(0);
    });

    test('keeps everything when the confirmation is cancelled or dismissed', async ({
      context,
      page,
    }) => {
      const removals = await watchRemovals(context);
      await openStorage(page, language);
      const trigger = removableRow(page).getByRole('button');

      await trigger.click();
      await page.getByRole('dialog').getByRole('button', { name: language.cancel }).click();
      await expect(page.getByRole('dialog')).toBeHidden();
      await expect(trigger).toBeFocused();

      await trigger.click();
      // Focus starts on the safe answer, so Escape reaches the dialog once it is open.
      await expect(
        page.getByRole('dialog').getByRole('button', { name: language.cancel }),
      ).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toBeHidden();
      await expect(trigger).toBeFocused();

      expect(removals).toEqual([]);
      await expect(page.locator('.totals')).toContainText(language.totalBefore);
      await expect(trigger).toBeEnabled();
    });

    test('reports a refused removal and keeps the row', async ({ context, page }) => {
      await watchRemovals(context);
      await context.route('**/api/v1/projects/*/source-video', (route) =>
        route.fulfill({
          status: 409,
          json: {
            error: { code: 'PACKAGE_REQUIRED', message: 'A package is required.', details: {} },
          },
        }),
      );
      await openStorage(page, language);
      await removableRow(page).getByRole('button').click();
      await page.getByRole('dialog').getByRole('button', { name: language.remove }).click();

      await expect(page.getByRole('alert')).toContainText(language.refused);
      await expect(page.locator('.totals')).toContainText(language.totalBefore);
      await expect(removableRow(page).getByRole('button')).toBeEnabled();
      const scan = await scanContrast(page);
      expect(scan.textFailures).toEqual([]);
      expect(scan.controlFailures).toEqual([]);
    });

    test('offers to try again after a failed load', async ({ context, page }) => {
      await watchRemovals(context);
      let failing = true;
      await context.route('**/api/v1/storage', async (route) => {
        if (failing) await route.abort();
        else await route.fallback();
      });
      await page.goto('/storage');

      const alert = page.getByRole('alert');
      await expect(alert).toContainText(language.unreachable);
      const scan = await scanContrast(page);
      expect(scan.textFailures).toEqual([]);

      failing = false;
      await alert.getByRole('button', { name: language.tryAgain }).click();
      await expect(alert).toBeHidden();
      await expect(page.locator('.totals')).toContainText(language.totalBefore);
    });

    test('explains an empty list', async ({ context, page }) => {
      await watchRemovals(context);
      await context.route('**/api/v1/storage', (route) =>
        route.fulfill({ json: { totalBytes: 0, freeBytes: null, items: [] } }),
      );
      await openStorage(page, language);

      await expect(page.getByText(language.empty)).toBeVisible();
      await expect(page.locator('.storage-row')).toHaveCount(0);
      const scan = await scanContrast(page);
      expect(scan.textFailures).toEqual([]);
    });
  });
}
