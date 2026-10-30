// Glossary field of the New project form (docs/UI.md; P09.1) in English and Italian; the same field in
// the Process dialog is covered in process.spec.ts.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { scanContrast } from './support/contrast';
import { useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    ready: 'Project name',
    moreOptions: 'More options',
    label: 'Glossary (optional)',
    help: 'Names and terms the speakers use, one per line.',
    create: 'Create project',
    videoStep: 'Choose video',
    summary: 'Some project details are not valid.',
    tooLong: 'A term is too long. Use at most 80 characters per line.',
    tooMany: 'The glossary has too many terms. Use at most 200.',
  },
  {
    code: 'it',
    locale: 'it-IT',
    ready: 'Nome del progetto',
    moreOptions: 'Altre opzioni',
    label: 'Glossario (facoltativo)',
    help: 'Nomi e termini usati da chi parla, uno per riga.',
    create: 'Crea progetto',
    videoStep: 'Scegli video',
    summary: 'Alcuni dettagli del progetto non sono validi.',
    tooLong: 'Un termine è troppo lungo. Usa al massimo 80 caratteri per riga.',
    tooMany: 'Il glossario ha troppi termini. Usane al massimo 200.',
  },
] as const;

const timestamp = '2026-10-30T10:00:00Z';

/** Sets up the fake API with a project creation route; returns the body of every creation. */
async function watchCreations(context: BrowserContext): Promise<{ glossary?: string[] }[]> {
  await useFakeApi(context, { status: 'review' });
  const bodies: { glossary?: string[] }[] = [];
  await context.route('**/api/v1/projects', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON() as { glossary?: string[] };
    bodies.push(body);
    await route.fulfill({
      status: 201,
      json: {
        ...body,
        id: 'project-new',
        detectedLanguage: null,
        status: 'draft',
        createdAt: timestamp,
        updatedAt: timestamp,
        videoAssetId: null,
        activeJobId: null,
        videoAsset: null,
        activeSegmentCount: 0,
        screenshotCount: 0,
      },
    });
  });
  return bodies;
}

/** Opens New project with "More options" expanded; returns the glossary field. */
async function openGlossary(page: Page, language: (typeof LANGUAGES)[number]) {
  await page.goto('/projects/new');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  await expect(page.getByText(language.ready).first()).toBeVisible();
  await page.getByText(language.moreOptions).click();
  const field = page.getByRole('textbox', { name: language.label });
  await expect(field).toBeVisible();
  return field;
}

for (const language of LANGUAGES) {
  test.describe(`New project glossary in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('is under More options and sends normalized terms', async ({ context, page }) => {
      const bodies = await watchCreations(context);
      await page.goto('/projects/new');
      await expect(page.locator('details.more-options')).not.toHaveAttribute('open', '');
      await expect(page.getByRole('textbox', { name: language.label })).toBeHidden();

      const field = await openGlossary(page, language);
      await expect(field).toHaveAccessibleDescription(new RegExp(language.help));
      await field.fill('  Valkey \n\nvalkey\nCittà   della Pieve\n');
      await page.getByRole('button', { name: language.create }).click();

      await expect(page.getByText(language.videoStep)).toBeVisible();
      expect(bodies).toHaveLength(1);
      expect(bodies[0]?.glossary).toEqual(['Valkey', 'Città della Pieve']);
    });

    test('sends an empty list when the field is left empty', async ({ context, page }) => {
      const bodies = await watchCreations(context);
      await page.goto('/projects/new');
      await page.getByRole('button', { name: language.create }).click();

      await expect(page.getByText(language.videoStep)).toBeVisible();
      expect(bodies[0]?.glossary).toEqual([]);
    });

    test('is reachable and usable with the keyboard', async ({ context, page }) => {
      const bodies = await watchCreations(context);
      await page.goto('/projects/new');
      const summary = page.locator('details.more-options summary');
      await summary.focus();
      await page.keyboard.press('Enter');
      const field = page.getByRole('textbox', { name: language.label });
      await expect(field).toBeVisible();
      // Spoken language and description come first.
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await expect(field).toBeFocused();
      await page.keyboard.type('Valkey');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Narrastep');
      await page.keyboard.press('Tab');
      await expect(page.getByRole('button', { name: language.create })).toBeFocused();
      await page.keyboard.press('Enter');

      await expect(page.getByText(language.videoStep)).toBeVisible();
      expect(bodies[0]?.glossary).toEqual(['Valkey', 'Narrastep']);
    });

    test('refuses a term that is too long and reopens More options', async ({ context, page }) => {
      const bodies = await watchCreations(context);
      const field = await openGlossary(page, language);
      await field.fill('x'.repeat(81));
      await page.getByText(language.moreOptions).click();
      await expect(field).toBeHidden();

      await page.getByRole('button', { name: language.create }).click();

      await expect(page.getByRole('alert')).toContainText(language.summary);
      await expect(field).toBeVisible();
      await expect(field).toHaveAttribute('aria-invalid', 'true');
      await expect(field).toHaveAccessibleDescription(new RegExp(language.tooLong));
      expect(bodies).toEqual([]);

      // A term of exactly the limit is accepted once the field is corrected.
      await field.fill('x'.repeat(80));
      await page.getByRole('button', { name: language.create }).click();
      await expect(page.getByText(language.videoStep)).toBeVisible();
      expect(bodies[0]?.glossary).toEqual(['x'.repeat(80)]);
    });

    test('refuses more than 200 terms', async ({ context, page }) => {
      const bodies = await watchCreations(context);
      const field = await openGlossary(page, language);
      await field.fill(Array.from({ length: 201 }, (_, index) => `term ${index}`).join('\n'));
      await page.getByRole('button', { name: language.create }).click();

      await expect(field).toHaveAccessibleDescription(new RegExp(language.tooMany));
      expect(bodies).toEqual([]);
    });

    test('keeps the field, its help and its error readable', async ({ context, page }) => {
      await watchCreations(context);
      const field = await openGlossary(page, language);
      await field.fill('x'.repeat(81));
      await page.getByRole('button', { name: language.create }).click();
      await expect(page.getByText(language.tooLong)).toBeVisible();

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
    test.describe(`New project glossary at ${viewport.label} in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
      test.use({
        locale: language.locale,
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.scale,
        contextOptions: { reducedMotion: 'reduce' },
      });

      test('fits with its error without horizontal scrolling', async ({ context, page }) => {
        await watchCreations(context);
        const field = await openGlossary(page, language);
        await field.fill('x'.repeat(81));
        await page.getByRole('button', { name: language.create }).click();
        const error = page.getByText(language.tooLong);
        await error.scrollIntoViewIfNeeded();
        await expect(error).toBeInViewport();

        const box = await field.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(-1);
        expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(1);
      });
    });
  }
}
