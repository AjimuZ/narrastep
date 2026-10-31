// Settings screen journeys (docs/UI.md, Settings; P06.4a): the model lifecycle with the license shown
// first, the language switch, and the kill switch that hides every control, in English and Italian.
import { expect, test, type Page } from '@playwright/test';

import { useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    title: 'Settings',
    quality: 'Quality',
    notInstalled: 'Not installed',
    downloading: 'Downloading',
    verifying: 'Verifying',
    installed: 'Installed',
    enabled: 'Enabled',
    install: 'Install the Quality model',
    accept: 'Accept the license and download',
    enable: 'Enable the Quality model',
    disable: 'Disable the Quality model',
    delete: 'Delete the Quality model',
    confirmDelete: 'Delete model',
    cancel: 'Cancel',
    cancelDownload: 'Cancel download of the Quality model',
    license: 'Apache License 2.0',
    size: '5.2 GB',
    deleted: 'The Quality model was deleted. 5.2 GB freed.',
    cancelled: 'Download of the Quality model cancelled.',
    switchTo: { name: 'Italiano', code: 'it', title: 'Impostazioni' },
    off: 'Local generation is turned off on this computer',
  },
  {
    code: 'it',
    locale: 'it-IT',
    title: 'Impostazioni',
    quality: 'Qualità',
    notInstalled: 'Non installato',
    downloading: 'Download in corso',
    verifying: 'Verifica in corso',
    installed: 'Installato',
    enabled: 'Attivo',
    install: 'Installa il modello Qualità',
    accept: 'Accetta la licenza e scarica',
    enable: 'Attiva il modello Qualità',
    disable: 'Disattiva il modello Qualità',
    delete: 'Elimina il modello Qualità',
    confirmDelete: 'Elimina modello',
    cancel: 'Annulla',
    cancelDownload: 'Annulla download del modello Qualità',
    license: 'Apache License 2.0',
    size: '5,2 GB',
    deleted: 'Il modello Qualità è stato eliminato. Liberati 5,2 GB.',
    cancelled: 'Download del modello Qualità annullato.',
    switchTo: { name: 'English', code: 'en', title: 'Settings' },
    off: 'La generazione locale è disattivata su questo computer',
  },
] as const;

/** Opens Settings and waits until the models have loaded. */
async function openSettings(page: Page, language: (typeof LANGUAGES)[number]): Promise<void> {
  await page.goto('/settings');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  await expect(page.getByRole('heading', { level: 1, name: language.title })).toBeVisible();
  await expect(page.locator('.model-row').first()).toBeVisible();
}

/** The row of the Quality model, the one that starts not installed. */
function qualityRow(page: Page, language: (typeof LANGUAGES)[number]) {
  return page.locator('.model-row').filter({ hasText: language.quality });
}

for (const language of LANGUAGES) {
  test.describe(`Settings in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('shows the license first, then follows the download until the model can be deleted', async ({
      context,
      page,
    }) => {
      // The download takes two polls of two seconds to finish, then several steps follow.
      test.setTimeout(60_000);
      await useFakeApi(context, { status: 'review', modelDownload: 'advancing' });
      const requests: string[] = [];
      context.on('request', (request) => {
        if (request.method() !== 'GET')
          requests.push(`${request.method()} ${new URL(request.url()).pathname}`);
      });
      await openSettings(page, language);
      const row = qualityRow(page, language);
      await expect(row.locator('.ns-pill')).toHaveText(language.notInstalled);

      // The license name, its link and the download size are on screen before anything is requested.
      await page.getByRole('button', { name: language.install }).click();
      const panel = row.locator('.model-install');
      await expect(panel).toContainText(language.license);
      await expect(panel).toContainText(language.size);
      await expect(panel.getByRole('link')).toHaveAttribute('href', /gemma_4_license/);
      expect(requests.filter((request) => request.includes('/models/'))).toEqual([]);

      await panel.getByRole('button', { name: language.accept }).click();
      await expect(row.locator('.ns-pill')).toHaveText(language.downloading);
      const gauge = row.locator('.model-progress [role="img"]');
      await expect(gauge).toHaveAttribute('aria-label', /40%|80%/);
      // The polled progress moves on to verification and ends in the installed state.
      await expect(row.locator('.ns-pill')).toHaveText(language.verifying, { timeout: 10_000 });
      await expect(row.locator('.ns-pill')).toHaveText(language.installed, { timeout: 10_000 });
      expect(requests).toContain('POST /api/v1/models/gemma-4-e4b/download');

      await row.getByRole('button', { name: language.enable }).click();
      await expect(row.locator('.ns-pill')).toHaveText(language.enabled);
      await row.getByRole('button', { name: language.disable }).click();
      await expect(row.locator('.ns-pill')).toHaveText(language.installed);

      // Deleting asks first: Escape keeps the model, the confirmation removes it.
      await row.getByRole('button', { name: language.delete }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByRole('button', { name: language.cancel })).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(row.locator('.ns-pill')).toHaveText(language.installed);
      expect(requests).not.toContain('DELETE /api/v1/models/gemma-4-e4b');

      await row.getByRole('button', { name: language.delete }).click();
      await dialog.getByRole('button', { name: language.confirmDelete }).click();
      await expect(row.locator('.ns-pill')).toHaveText(language.notInstalled);
      await expect(page.getByRole('status').filter({ hasText: language.deleted })).toBeVisible();
    });

    test('cancels a download that is in progress', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      await openSettings(page, language);
      const row = qualityRow(page, language);

      await page.getByRole('button', { name: language.install }).click();
      await row.getByRole('button', { name: language.accept }).click();
      await expect(row.locator('.ns-pill')).toHaveText(language.downloading);
      await row.getByRole('button', { name: language.cancelDownload }).click();

      await expect(row.locator('.ns-pill')).toHaveText(language.notInstalled);
      await expect(page.getByRole('status').filter({ hasText: language.cancelled })).toBeVisible();
    });

    test('switches the interface language from Settings and keeps the screen', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      await openSettings(page, language);
      const options = page.locator('.language-options');
      await expect(
        options.getByRole('button', { name: language.code === 'en' ? 'English' : 'Italiano' }),
      ).toHaveAttribute('aria-current', 'true');

      await options.getByRole('button', { name: language.switchTo.name }).click();

      await expect(page.locator('html')).toHaveAttribute('lang', language.switchTo.code);
      await expect(page).toHaveURL(/\/settings$/);
      await expect(
        page.getByRole('heading', { level: 1, name: language.switchTo.title }),
      ).toBeVisible();
      expect(await page.evaluate(() => localStorage.getItem('narrastep.language'))).toBe(
        language.switchTo.code,
      );
    });

    test('hides every model control when local generation is turned off', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review', localGeneration: 'disabled' });
      await page.goto('/settings');
      await expect(page.locator('html')).toHaveAttribute('lang', language.code);
      const generation = page.locator('.generation-window');
      await expect(generation).toContainText(language.off);

      await expect(generation.getByRole('button')).toHaveCount(0);
      await expect(generation.getByRole('link')).toHaveCount(0);
      await expect(page.locator('.model-row')).toHaveCount(0);
      // The language switch is not a model control and stays.
      await expect(page.locator('.language-options').getByRole('button')).toHaveCount(2);
    });
  });
}
