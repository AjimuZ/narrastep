// Speaker labels in Review (docs/UI.md, Review; P05.5): the automatic You / Others label is shown,
// changed and cleared from the keyboard, a failed save is reported, in English and Italian.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { scanContrast } from './support/contrast';
import { useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    speaker: 'Speaker',
    you: 'You',
    others: 'Others',
    automatic: 'Set from the audio levels',
    changed: 'Speaker of segment 1: Others.',
    cleared: 'Speaker label removed from segment 1.',
    // English shows the API's own message; other languages show the translation of the code.
    busy: 'The project has active background work.',
  },
  {
    code: 'it',
    locale: 'it-IT',
    speaker: 'Chi parla',
    you: 'Tu',
    others: 'Altri',
    automatic: 'Impostata dai livelli audio',
    changed: 'Chi parla nel segmento 1: Altri.',
    cleared: 'Etichetta di chi parla rimossa dal segmento 1.',
    busy: "Il progetto è occupato con un'elaborazione o un pacchetto. Attendi che finisca, poi riprova.",
  },
] as const;

/** Records the bodies of every segment PATCH; with `fail` the API refuses it as busy. */
async function watchSegmentSaves(
  context: BrowserContext,
  options: { fail?: boolean } = {},
): Promise<unknown[]> {
  await useFakeApi(context, { status: 'review' });
  const bodies: unknown[] = [];
  await context.route('**/api/v1/segments/*', async (route) => {
    if (route.request().method() !== 'PATCH') {
      await route.fallback();
      return;
    }
    bodies.push(route.request().postDataJSON());
    if (options.fail) {
      // An export started meanwhile, for example in another window.
      await route.fulfill({
        status: 409,
        json: {
          error: {
            code: 'PROJECT_BUSY',
            message: 'The project has active background work.',
            details: {},
          },
        },
      });
    } else {
      await route.fallback();
    }
  });
  return bodies;
}

/** Opens Review of the fake project and returns the speaker select of the first segment. */
async function openReview(page: Page, language: (typeof LANGUAGES)[number]) {
  await page.goto('/projects/project-1/review');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  const select = page.getByRole('combobox', { name: language.speaker, exact: true });
  await expect(select).toHaveValue('you');
  return select;
}

for (const language of LANGUAGES) {
  test.describe(`speaker labels in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('shows the automatic label, changes and clears it with the keyboard', async ({
      context,
      page,
    }) => {
      const bodies = await watchSegmentSaves(context);
      const select = await openReview(page, language);
      const transcript = page.locator('#review-transcript-panel');
      const firstRow = page.locator('.segment-rail__item').first();
      await expect(transcript).toContainText(language.automatic);
      await expect(firstRow).toContainText(language.you);
      await expect(select.locator('option')).toHaveText([
        language.code === 'en' ? 'No label' : 'Nessuna etichetta',
        language.you,
        language.others,
      ]);

      // Keyboard: focus the select and move to the next option without opening it.
      await select.focus();
      await page.keyboard.press('ArrowDown');
      await expect(select).toHaveValue('others');
      await expect.poll(() => bodies).toEqual([{ speakerLabel: 'others' }]);
      await expect(page.locator('[aria-live="polite"]').first()).toHaveText(language.changed);
      await expect(firstRow).toContainText(language.others);
      await expect(transcript).not.toContainText(language.automatic);
      // The select stays enabled while saving, so focus does not jump away.
      await expect(select).toBeFocused();

      await select.selectOption('');
      await expect.poll(() => bodies).toEqual([{ speakerLabel: 'others' }, { speakerLabel: null }]);
      await expect(page.locator('[aria-live="polite"]').first()).toHaveText(language.cleared);
      await expect(firstRow).not.toContainText(language.others);

      // The label survives a reload, because it was saved.
      await page.reload();
      await expect(page.getByRole('combobox', { name: language.speaker, exact: true })).toHaveValue(
        '',
      );
    });

    test('puts the label back and reports a save that fails', async ({ context, page }) => {
      const bodies = await watchSegmentSaves(context, { fail: true });
      const select = await openReview(page, language);

      await select.selectOption('others');

      await expect(page.getByRole('alert')).toContainText(language.busy);
      await expect(select).toHaveValue('you');
      expect(bodies).toEqual([{ speakerLabel: 'others' }]);
      await expect(page.locator('.segment-rail__item').first()).toContainText(language.you);
    });

    test('keeps the speaker select and its hint readable', async ({ context, page }) => {
      await watchSegmentSaves(context);
      await openReview(page, language);
      await page.locator('.speaker-field select').hover();

      const scan = await scanContrast(page);
      expect(scan.checked).toBeGreaterThan(5);
      expect(scan.textFailures).toEqual([]);
      expect(scan.controlFailures).toEqual([]);
    });
  });

  for (const viewport of [
    { label: '320 px', width: 320, height: 640, scale: 1 },
    { label: '200% zoom of 1280 px', width: 640, height: 360, scale: 2 },
  ]) {
    test.describe(`speaker select at ${viewport.label} in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
      test.use({
        locale: language.locale,
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.scale,
        contextOptions: { reducedMotion: 'reduce' },
      });

      test('fits inside the transcript window without horizontal scrolling', async ({
        context,
        page,
      }) => {
        await watchSegmentSaves(context);
        const select = await openReview(page, language);
        await select.scrollIntoViewIfNeeded();
        await expect(select).toBeInViewport();

        const panel = await page.locator('#review-transcript-panel').boundingBox();
        const field = await page.locator('.speaker-field').boundingBox();
        expect(panel).not.toBeNull();
        expect(field).not.toBeNull();
        expect(field!.x).toBeGreaterThanOrEqual(panel!.x - 1);
        expect(field!.x + field!.width).toBeLessThanOrEqual(panel!.x + panel!.width + 1);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(1);
      });
    });
  }
}
