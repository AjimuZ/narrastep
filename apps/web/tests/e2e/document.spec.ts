// Document screen journeys (docs/UI.md, Document; P06.4b): generate with real progress per part, the
// result beside the video, regenerate, cancel, and the screen being absent when generation is off.
import { expect, test, type Page } from '@playwright/test';

import { useFakeApi } from './support/fake-api';

const LANGUAGES = [
  {
    code: 'en',
    locale: 'en-US',
    title: 'Document',
    review: 'Review',
    generate: 'Generate document',
    regenerate: 'Regenerate',
    cancel: 'Cancel generation',
    waiting: 'Waiting to start',
    generating: 'Generating',
    completed: 'Completed',
    cancelled: 'Cancelled',
    failed: 'Failed',
    notGenerated: 'Not generated',
    parts: /Parts written: [0-3] of 3/,
    partsTwo: 'Parts written: 2 of 3',
    summary: '1. Summary',
    ready: 'The document is ready.',
    memory: 'The worker has less free memory than the model needs.',
    documentLink: 'Document',
  },
  {
    code: 'it',
    locale: 'it-IT',
    title: 'Documento',
    review: 'Revisione',
    generate: 'Genera documento',
    regenerate: 'Rigenera',
    cancel: 'Annulla generazione',
    waiting: 'In attesa di iniziare',
    generating: 'In generazione',
    completed: 'Completato',
    cancelled: 'Annullato',
    failed: 'Non riuscito',
    notGenerated: 'Non generato',
    parts: /Parti scritte: [0-3] di 3/,
    partsTwo: 'Parti scritte: 2 di 3',
    summary: '1. Summary',
    ready: 'Il documento è pronto.',
    memory: "Non c'è abbastanza memoria libera",
    documentLink: 'Documento',
  },
] as const;

type Language = (typeof LANGUAGES)[number];

/** Opens the Document screen of project-1 and waits until it has loaded. */
async function openDocument(page: Page, language: Language): Promise<void> {
  await page.goto('/projects/project-1/document');
  await expect(page.locator('html')).toHaveAttribute('lang', language.code);
  await expect(page.getByRole('heading', { level: 1, name: language.title })).toBeVisible();
  await expect(page.locator('.generation-window')).toBeVisible();
}

for (const language of LANGUAGES) {
  test.describe(`Document in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale, contextOptions: { reducedMotion: 'reduce' } });

    test('generates with progress per part and shows the result beside the video', async ({
      context,
      page,
    }) => {
      // The fake moves one step at every two-second poll and finishes after five polls.
      test.setTimeout(60_000);
      await useFakeApi(context, { status: 'review', lightEnabled: true });
      const requests: string[] = [];
      context.on('request', (request) => {
        if (request.method() !== 'GET')
          requests.push(`${request.method()} ${new URL(request.url()).pathname}`);
      });
      await openDocument(page, language);
      const status = page.locator('.status-head .ns-pill');
      await expect(status).toHaveText(language.notGenerated);
      await expect(page.locator('.document-text')).toHaveCount(0);

      await page.getByRole('button', { name: language.generate }).click();
      expect(requests).toContain('POST /api/v1/projects/project-1/generations');
      await expect(status).toHaveText(language.generating, { timeout: 10_000 });
      // Progress comes from the finished parts the API reports, not from a timer.
      await expect(page.locator('.progress__parts')).toHaveText(language.parts);
      await expect(page.locator('.progress__parts')).toHaveText(language.partsTwo, {
        timeout: 10_000,
      });
      await expect(page.locator('.progress [role="img"]')).toHaveAttribute('aria-label', /57%/);

      await expect(status).toHaveText(language.completed, { timeout: 15_000 });
      await expect(page.getByRole('status').filter({ hasText: language.ready })).toBeVisible();
      const article = page.locator('.document-text');
      await expect(article.getByRole('heading', { name: language.summary })).toBeVisible();
      await expect(article).toContainText('[00:06-00:12]');
      await expect(article).not.toContainText('promptVersion');

      // The video sits beside the text on a wide window and plays the project's source.
      const video = page.locator('.video-window video');
      await expect(video).toHaveAttribute('src', '/api/v1/projects/project-1/video');
      const textBox = await page.locator('.document-window').boundingBox();
      const videoBox = await page.locator('.video-window').boundingBox();
      if ((page.viewportSize()?.width ?? 0) >= 1024) {
        expect(videoBox!.x).toBeGreaterThan(textBox!.x + textBox!.width - 1);
      } else {
        // A narrow window stacks the two windows instead of squeezing them.
        expect(videoBox!.y).toBeGreaterThan(textBox!.y + textBox!.height - 1);
      }

      // Nothing offers copying or the package yet.
      await expect(page.getByRole('button', { name: /copy|copia|package|pacchetto/i })).toHaveCount(
        0,
      );
    });

    test('shows an existing document and regenerates it while the old text stays', async ({
      context,
      page,
    }) => {
      test.setTimeout(60_000);
      await useFakeApi(context, {
        status: 'review',
        lightEnabled: true,
        generationExisting: true,
        generationRun: 'stalled',
      });
      await openDocument(page, language);
      await expect(page.locator('.status-head .ns-pill')).toHaveText(language.completed);
      await expect(page.locator('.document-text')).toContainText('The customer opens');

      await page.getByRole('button', { name: language.regenerate }).click();
      await expect(page.getByRole('button', { name: language.cancel })).toBeVisible();
      await expect(page.locator('.progress__parts')).toHaveText(language.parts, {
        timeout: 10_000,
      });
      await expect(page.locator('.document-text')).toContainText('The customer opens');
      await expect(page.getByRole('button', { name: language.regenerate })).toHaveCount(0);
    });

    test('cancels a running generation', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review', lightEnabled: true, generationRun: 'stalled' });
      await openDocument(page, language);

      await page.getByRole('button', { name: language.generate }).click();
      await expect(page.locator('.progress__parts')).toBeVisible({ timeout: 10_000 });
      await page.getByRole('button', { name: language.cancel }).click();

      await expect(page.locator('.status-head .ns-pill')).toHaveText(language.cancelled);
      await expect(page.getByRole('button', { name: language.generate })).toBeEnabled();
      await expect(page.locator('.document-text')).toHaveCount(0);
    });

    test('explains a failed generation by its code', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review', lightEnabled: true, generationRun: 'failing' });
      await openDocument(page, language);

      await page.getByRole('button', { name: language.generate }).click();

      await expect(page.locator('.status-head .ns-pill')).toHaveText(language.failed, {
        timeout: 15_000,
      });
      const failure = page.locator('.failure');
      await expect(failure).toContainText('GENERATION_OUT_OF_MEMORY');
      await expect(failure).toContainText(language.memory);
      await expect(page.getByRole('button', { name: language.generate })).toBeEnabled();
    });

    test('links to the Document screen from the review only while a model is enabled', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review', lightEnabled: true });
      await page.goto('/projects/project-1/review');
      const link = page.locator('.page-header__actions').getByRole('link', {
        name: language.documentLink,
      });
      await expect(link).toBeVisible();
      await link.click();
      await expect(page).toHaveURL(/\/projects\/project-1\/document$/);
      await expect(page.getByRole('heading', { level: 1, name: language.title })).toBeVisible();
    });

    test('is absent while no model is enabled: no link, and the route returns to the review', async ({
      context,
      page,
    }) => {
      // Light is installed but not enabled, so generation cannot run.
      await useFakeApi(context, { status: 'review' });
      await page.goto('/projects/project-1/review');
      await expect(page.getByRole('button', { name: /^(Save text|Salva testo)/ })).toBeVisible();
      await expect(
        page.locator('.page-header__actions').getByRole('link', { name: language.documentLink }),
      ).toHaveCount(0);

      await page.goto('/projects/project-1/document');
      await expect(page).toHaveURL(/\/projects\/project-1\/review$/);
      await expect(page.getByRole('heading', { level: 1, name: language.review })).toBeVisible();
    });

    test('is absent when local generation is turned off', async ({ context, page }) => {
      await useFakeApi(context, {
        status: 'review',
        lightEnabled: true,
        localGeneration: 'disabled',
      });
      await page.goto('/projects/project-1/review');
      await expect(page.getByRole('button', { name: /^(Save text|Salva testo)/ })).toBeVisible();
      await expect(
        page.locator('.page-header__actions').getByRole('link', { name: language.documentLink }),
      ).toHaveCount(0);

      await page.goto('/projects/project-1/document');
      await expect(page).toHaveURL(/\/projects\/project-1\/review$/);
    });
  });
}
