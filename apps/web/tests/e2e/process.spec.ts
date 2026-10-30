// Process dialog on a saved recording (docs/UI.md, Process): the project type is asked when processing
// starts, not before; keyboard, contrast and reflow in English and Italian.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { scanContrast } from './support/contrast';
import { useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    process: 'Process',
    start: 'Start processing',
    cancel: 'Cancel',
    notChosen: 'Not chosen',
    meeting: 'Meeting',
    typeError: 'Choose a project type to start processing.',
    moreOptions: 'More options',
    language: 'Spoken language',
    glossary: 'Glossary (optional)',
    glossaryHelp: 'Names and terms the speakers use, one per line.',
    glossaryTooLong: 'A term is too long. Use at most 80 characters per line.',
    glossaryTooMany: 'The glossary has too many terms. Use at most 200.',
  },
  {
    code: 'it',
    locale: 'it-IT',
    process: 'Elabora',
    start: 'Avvia elaborazione',
    cancel: 'Annulla',
    notChosen: 'Non scelto',
    meeting: 'Riunione',
    typeError: "Scegli un tipo di progetto per avviare l'elaborazione.",
    moreOptions: 'Altre opzioni',
    language: 'Lingua parlata',
    glossary: 'Glossario (facoltativo)',
    glossaryHelp: 'Nomi e termini usati da chi parla, uno per riga.',
    glossaryTooLong: 'Un termine è troppo lungo. Usa al massimo 80 caratteri per riga.',
    glossaryTooMany: 'Il glossario ha troppi termini. Usane al massimo 200.',
  },
] as const;

const RECORDING_NAME = 'Recording 2026-10-29 14:05';
const timestamp = '2026-10-29T14:05:00Z';

interface ProcessApi {
  /** Bodies of every `POST /projects/rec-1/process`, in order. */
  starts: unknown[];
}

/**
 * Adds a saved recording without a project type, and with the given saved glossary, to the fake API
 * and records processing starts.
 */
async function useSavedRecording(
  context: BrowserContext,
  glossary: string[] = [],
): Promise<ProcessApi> {
  await useFakeApi(context, { status: 'review' });
  const api: ProcessApi = { starts: [] };
  const project = () => ({
    id: 'rec-1',
    name: RECORDING_NAME,
    description: null,
    mode: api.starts.length > 0 ? 'meeting' : null,
    sourceLanguage: 'auto',
    glossary,
    detectedLanguage: null,
    status: api.starts.length > 0 ? 'queued' : 'uploaded',
    createdAt: timestamp,
    updatedAt: timestamp,
    videoAssetId: 'asset-rec',
    activeJobId: api.starts.length > 0 ? 'job-rec' : null,
    videoAsset: {
      id: 'asset-rec',
      projectId: 'rec-1',
      assetType: 'source_video',
      originalFilename: 'source.webm',
      mimeType: 'video/webm',
      sizeBytes: 100_000,
      durationSeconds: 6,
      width: 1280,
      height: 720,
      createdAt: timestamp,
    },
    activeSegmentCount: 0,
    screenshotCount: 0,
    recording: { id: 'rec-1-session', state: 'completed', interrupted: false },
  });
  await context.route('**/api/v1/projects**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === '/api/v1/projects/rec-1/process' && method === 'POST') {
      api.starts.push(route.request().postDataJSON());
      await route.fulfill({
        status: 202,
        json: {
          id: 'job-rec',
          projectId: 'rec-1',
          status: 'queued',
          currentStage: 'queued',
          progressPercent: 0,
          message: 'Queued.',
          errorCode: null,
          errorMessage: null,
          startedAt: null,
          completedAt: null,
          createdAt: timestamp,
          version: 1,
        },
      });
    } else if (url.pathname === '/api/v1/projects' && method === 'GET') {
      const items = [project()];
      await route.fulfill({ json: { items, total: items.length } });
    } else if (url.pathname === '/api/v1/projects/rec-1' && method === 'GET') {
      await route.fulfill({ json: project() });
    } else {
      await route.fallback();
    }
  });
  return api;
}

/** Opens Projects and the Process dialog of the saved recording; returns the dialog and its trigger. */
async function openProcessDialog(page: Page, language: (typeof LANGUAGES)[number]) {
  await page.goto('/projects');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  const row = page.locator('.project-row').filter({ hasText: RECORDING_NAME });
  await expect(row.locator('[data-label="Type"], [data-label="Tipo"]')).toContainText(
    language.notChosen,
  );
  const trigger = row.getByRole('button', { name: new RegExp(`^${language.process} `) });
  await trigger.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  return { dialog, trigger };
}

for (const language of LANGUAGES) {
  test.describe(`Process dialog in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('asks for the type, refuses to start without it and starts with the chosen one', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context);
      const { dialog } = await openProcessDialog(page, language);

      // Starting without a type keeps the dialog open and sends nothing.
      await dialog.getByRole('button', { name: language.start }).click();
      await expect(dialog.getByRole('alert')).toHaveText(language.typeError);
      expect(api.starts).toEqual([]);

      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByRole('button', { name: language.start }).click();

      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting' }]);
    });

    test('closes with Escape or Cancel, returns focus and starts nothing', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context);
      const { dialog, trigger } = await openProcessDialog(page, language);

      // Focus starts inside the dialog and stays there with Tab and Shift+Tab.
      for (let press = 0; press < 12; press += 1) {
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
        await page.keyboard.press('Tab');
      }
      for (let press = 0; press < 3; press += 1) {
        await page.keyboard.press('Shift+Tab');
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
      }

      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(trigger).toBeFocused();

      await trigger.click();
      await page.getByRole('dialog').getByRole('button', { name: language.cancel }).click();
      await expect(page.getByRole('dialog')).toBeHidden();
      expect(api.starts).toEqual([]);
    });

    test('keeps the language under More options and sends a changed one', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context);
      const { dialog } = await openProcessDialog(page, language);

      const details = dialog.locator('details.more-options');
      await expect(details).not.toHaveAttribute('open', '');
      await dialog.getByText(language.moreOptions).click();
      await dialog.getByLabel(language.language).fill('it');
      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByRole('button', { name: language.start }).click();

      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting', sourceLanguage: 'it' }]);
    });

    test('shows the saved glossary and sends it only when it changed', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context, ['Valkey', 'Città della Pieve']);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByText(language.moreOptions).click();
      const field = dialog.getByRole('textbox', { name: language.glossary });
      await expect(field).toHaveValue('Valkey\nCittà della Pieve');
      await expect(field).toHaveAccessibleDescription(new RegExp(language.glossaryHelp));

      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByRole('button', { name: language.start }).click();
      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting' }]);
    });

    test('sends a changed glossary as normalized terms', async ({ context, page }) => {
      const api = await useSavedRecording(context, ['Valkey']);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByText(language.moreOptions).click();
      await dialog
        .getByRole('textbox', { name: language.glossary })
        .fill('  Valkey \n\nvalkey\nCittà   della Pieve\n');
      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByRole('button', { name: language.start }).click();

      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting', glossary: ['Valkey', 'Città della Pieve'] }]);
    });

    test('clears a saved glossary with an empty field', async ({ context, page }) => {
      const api = await useSavedRecording(context, ['Valkey']);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByText(language.moreOptions).click();
      await dialog.getByRole('textbox', { name: language.glossary }).fill('  \n ');
      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByRole('button', { name: language.start }).click();

      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting', glossary: [] }]);
    });

    test('refuses a glossary that breaks a limit, reopens More options and starts nothing', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.getByText(language.moreOptions).click();
      const field = dialog.getByRole('textbox', { name: language.glossary });
      await field.fill('x'.repeat(81));
      await dialog.getByText(language.moreOptions).click();
      await expect(field).toBeHidden();

      await dialog.getByRole('button', { name: language.start }).click();

      await expect(field).toBeVisible();
      await expect(field).toHaveAttribute('aria-invalid', 'true');
      await expect(field).toHaveAccessibleDescription(new RegExp(language.glossaryTooLong));

      await field.fill(Array.from({ length: 201 }, (_, index) => `term ${index}`).join('\n'));
      await expect(field).toHaveAccessibleDescription(new RegExp(language.glossaryTooMany));
      await dialog.getByRole('button', { name: language.start }).click();
      await expect(dialog).toBeVisible();
      expect(api.starts).toEqual([]);

      const scan = await scanContrast(page);
      expect(scan.textFailures).toEqual([]);
      expect(scan.controlFailures).toEqual([]);
    });

    test('reaches the glossary with the keyboard and keeps focus in the dialog', async ({
      context,
      page,
    }) => {
      const api = await useSavedRecording(context);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByRole('radio', { name: new RegExp(language.meeting) }).check();
      await dialog.locator('details.more-options summary').focus();
      await page.keyboard.press('Enter');
      const field = dialog.getByRole('textbox', { name: language.glossary });
      await expect(field).toBeVisible();
      // The spoken language comes first.
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await expect(field).toBeFocused();
      await page.keyboard.type('Valkey');
      await page.keyboard.press('Tab');
      await expect(dialog.getByRole('button', { name: language.cancel })).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(dialog.getByRole('button', { name: language.start })).toBeFocused();
      await page.keyboard.press('Enter');

      await expect(page).toHaveURL(/\/projects\/rec-1$/);
      expect(api.starts).toEqual([{ mode: 'meeting', glossary: ['Valkey'] }]);
    });

    test('keeps text and controls readable, including the error state', async ({
      context,
      page,
    }) => {
      await useSavedRecording(context);
      const { dialog } = await openProcessDialog(page, language);
      await dialog.getByRole('button', { name: language.start }).click();
      await expect(dialog.getByRole('alert')).toBeVisible();

      const scan = await scanContrast(page);
      expect(scan.checked).toBeGreaterThan(5);
      expect(scan.textFailures).toEqual([]);
      expect(scan.controlFailures).toEqual([]);
    });
  });

  for (const viewport of [
    { label: '320 px', width: 320, height: 640, scale: 1 },
    { label: '200% zoom of 1280 px', width: 640, height: 360, scale: 2 },
    { label: '400% zoom of 1280 px', width: 320, height: 180, scale: 4 },
  ]) {
    test.describe(`Process dialog at ${viewport.label} in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
      test.use({
        locale: language.locale,
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.scale,
        contextOptions: { reducedMotion: 'reduce' },
      });

      test('fits without horizontal scrolling and keeps every action reachable', async ({
        context,
        page,
      }) => {
        await useSavedRecording(context);
        const { dialog } = await openProcessDialog(page, language);

        const box = await dialog.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(-1);
        expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
        // The dialog scrolls inside itself when it is taller than the viewport, so the action
        // buttons must still be reachable by scrolling.
        const start = dialog.getByRole('button', { name: language.start });
        await start.scrollIntoViewIfNeeded();
        await expect(start).toBeInViewport();
        const overflow = await dialog.evaluate((node) => node.scrollWidth - node.clientWidth);
        expect(overflow).toBeLessThanOrEqual(1);
      });

      test('fits with the glossary and its error open', async ({ context, page }) => {
        await useSavedRecording(context);
        const { dialog } = await openProcessDialog(page, language);
        await dialog.getByText(language.moreOptions).click();
        const field = dialog.getByRole('textbox', { name: language.glossary });
        await field.fill('x'.repeat(81));
        await dialog.getByRole('button', { name: language.start }).click();
        const error = dialog.getByText(language.glossaryTooLong);
        await error.scrollIntoViewIfNeeded();
        await expect(error).toBeInViewport();

        const box = await field.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(-1);
        expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
        const overflow = await dialog.evaluate((node) => node.scrollWidth - node.clientWidth);
        expect(overflow).toBeLessThanOrEqual(1);
      });
    });
  }
}
