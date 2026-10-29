import { expect, test, type Page } from '@playwright/test';

const timestamp = '2026-07-15T10:00:00Z';

/** Interface words the journey looks for, per interface language (docs/UI.md, fixed terms). */
const COPY = {
  en: {
    servicesReady: 'Services ready',
    noProjects: 'No projects yet',
    newProject: 'New project',
    mainMenu: 'Main menu',
    projectName: 'Project name',
    moreOptions: 'More options',
    description: 'Description (optional)',
    createProject: 'Create project',
    chooseVideo: 'Choose video',
    uploadVideo: 'Upload video',
    readyToProcess: /Ready to process/,
    startProcessing: 'Start processing',
    openReview: 'Open review',
    viewSwitcher: 'Review workspace view',
    video: 'Video',
    transcript: 'Transcript',
    screenshot: 'Screenshot',
    recoverFrame: 'Recover current frame',
    frameSaved: 'Current video frame saved as primary evidence.',
    reviewedText: 'Reviewed text for segment 1',
    saveText: 'Save text',
    splitAtCaret: 'Split at caret',
    splitSegment: 'Split segment',
    twoSegments: '2 segments',
    oneSegment: '1 segment',
    mergeCheckbox: /^Merge segment \d+$/,
    mergeAdjacent: 'Merge adjacent',
    mergeSegments: 'Merge segments',
    useAsPrimary: 'Use as primary',
    userSelected: 'User selected',
    export: 'Export',
    batchFile: '04-chatgpt-batches/batch-NN-of-NN/batch-input.md + up to 19 images',
    createPackage: 'Create package',
    downloadPackage: 'Download package',
  },
  it: {
    servicesReady: 'Servizi pronti',
    noProjects: 'Ancora nessun progetto',
    newProject: 'Nuovo progetto',
    mainMenu: 'Menu principale',
    projectName: 'Nome del progetto',
    moreOptions: 'Altre opzioni',
    description: 'Descrizione (facoltativa)',
    createProject: 'Crea progetto',
    chooseVideo: 'Scegli video',
    uploadVideo: 'Carica video',
    readyToProcess: /Pronto per elaborare/,
    startProcessing: 'Avvia elaborazione',
    openReview: 'Apri revisione',
    viewSwitcher: "Vista dell'area di revisione",
    video: 'Video',
    transcript: 'Trascrizione',
    screenshot: 'Screenshot',
    recoverFrame: 'Recupera il fotogramma attuale',
    frameSaved: 'Fotogramma attuale del video salvato come prova principale.',
    reviewedText: 'Testo rivisto del segmento 1',
    saveText: 'Salva testo',
    splitAtCaret: 'Dividi al cursore',
    splitSegment: 'Dividi segmento',
    twoSegments: '2 segmenti',
    oneSegment: '1 segmento',
    mergeCheckbox: /^Unisci il segmento \d+$/,
    mergeAdjacent: 'Unisci adiacenti',
    mergeSegments: 'Unisci segmenti',
    useAsPrimary: 'Usa come principale',
    userSelected: "Scelto dall'utente",
    export: 'Esporta',
    batchFile: '04-chatgpt-batches/batch-NN-of-NN/batch-input.md + fino a 19 immagini',
    createPackage: 'Crea pacchetto',
    downloadPackage: 'Scarica pacchetto',
  },
} as const;
type Copy = (typeof COPY)[keyof typeof COPY];
const projectName = 'Contract search and customer onboarding workflow for operations';

/** Confirms that the current route never creates horizontal document scrolling. */
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflowPixels = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflowPixels).toBeLessThanOrEqual(1);
}

/** Exercises the three responsive review arrangements at the supported viewport boundaries. */
async function verifyReviewLayoutMatrix(page: Page, t: Copy): Promise<void> {
  const viewports = [
    { width: 320, height: 720 },
    { width: 390, height: 844 },
    { width: 768, height: 900 },
    { width: 1024, height: 900 },
    { width: 1280, height: 900 },
    { width: 1440, height: 900 },
  ];
  const switcher = page.getByRole('radiogroup', { name: t.viewSwitcher });
  const structurePanel = page.locator('#review-structure-panel');
  const videoPanel = page.locator('#review-video-panel');
  const transcriptPanel = page.locator('#review-transcript-panel');
  const screenshotPanel = page.locator('#review-screenshot-panel');

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await expectNoHorizontalOverflow(page);

    if (viewport.width < 768) {
      await expect(switcher).toBeVisible();
      await expect(videoPanel).toBeHidden();
      await expect(structurePanel).toBeVisible();
      await expect(transcriptPanel).toBeVisible();
      await expect(screenshotPanel).toBeHidden();
      continue;
    }

    if (viewport.width < 1216) {
      await expect(switcher).toBeVisible();
      await expect(videoPanel).toBeVisible();
      await expect(structurePanel).toBeVisible();
      await expect(transcriptPanel).toBeVisible();
      await expect(screenshotPanel).toBeHidden();
      const videoBox = await videoPanel.boundingBox();
      const transcriptBox = await transcriptPanel.boundingBox();
      expect(videoBox).not.toBeNull();
      expect(transcriptBox).not.toBeNull();
      if (videoBox && transcriptBox && viewport.width < 1024) {
        expect(transcriptBox.y).toBeLessThan(videoBox.y);
      }
      if (videoBox && transcriptBox && viewport.width >= 1024) {
        expect(transcriptBox.x).toBeLessThan(videoBox.x);
      }
      await switcher.getByRole('radio', { name: t.screenshot }).check({ force: true });
      await expect(transcriptPanel).toBeVisible();
      await expect(structurePanel).toBeVisible();
      await expect(videoPanel).toBeHidden();
      await expect(screenshotPanel).toBeVisible();
      await switcher.getByRole('radio', { name: t.video }).check({ force: true });
      continue;
    }

    await expect(switcher).toBeHidden();
    await expect(structurePanel).toBeVisible();
    await expect(videoPanel).toBeVisible();
    await expect(transcriptPanel).toBeVisible();
    await expect(screenshotPanel).toBeVisible();

    const videoBox = await videoPanel.boundingBox();
    const structureBox = await structurePanel.boundingBox();
    const transcriptBox = await transcriptPanel.boundingBox();
    const screenshotBox = await screenshotPanel.boundingBox();
    expect(videoBox).not.toBeNull();
    expect(structureBox).not.toBeNull();
    expect(transcriptBox).not.toBeNull();
    expect(screenshotBox).not.toBeNull();

    if (!videoBox || !structureBox || !transcriptBox || !screenshotBox) {
      continue;
    }
    expect(structureBox.x).toBeLessThan(transcriptBox.x);
    expect(transcriptBox.x).toBeLessThan(videoBox.x);
    expect(Math.abs(videoBox.x - screenshotBox.x)).toBeLessThanOrEqual(1);
    expect(videoBox.y).toBeLessThan(screenshotBox.y);
  }
}

/** Builds a transcript segment used by the deterministic browser contract test. */
function segment(id: string, index: number, start: number, end: number, text: string) {
  return {
    id,
    projectId: 'project-1',
    segmentIndex: index,
    startSeconds: start,
    endSeconds: end,
    speakerLabel: null,
    rawText: text,
    reviewedText: text,
    confidence: 0.92,
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

for (const language of ['en', 'it'] as const) {
  test.describe(`in ${language === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language === 'en' ? 'en-US' : 'it-IT' });

    test('creates, reviews, restructures, reassigns evidence, and exports a project', async ({
      context,
      page,
    }, testInfo) => {
      const t = COPY[language];
      let projectCreated = false;
      let projectStatus = 'draft';
      let activeSegments = [segment('segment-1', 0, 0, 6, 'Select Search and review results.')];
      let primaryScreenshotId = 'shot-1';
      let exportHistory: object[] = [];

      const project = () => ({
        id: 'project-1',
        name: projectName,
        description: 'Search workflow',
        mode: 'tutorial',
        sourceLanguage: 'en',
        detectedLanguage: 'en',
        status: projectStatus,
        createdAt: timestamp,
        updatedAt: timestamp,
        videoAssetId: projectStatus === 'draft' ? null : 'asset-1',
        activeJobId: null,
      });

      await context.route('**/api/v1/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        const method = request.method();

        if (path === '/api/v1/health') {
          await route.fulfill({
            json: { status: 'ok', version: '0.2.0', database: 'ok', redis: 'ok', worker: 'ok' },
          });
          return;
        }
        if (path === '/api/v1/projects' && method === 'GET') {
          await route.fulfill({
            json: { items: projectCreated ? [project()] : [], total: projectCreated ? 1 : 0 },
          });
          return;
        }
        if (path === '/api/v1/projects' && method === 'POST') {
          projectCreated = true;
          await route.fulfill({ status: 201, json: project() });
          return;
        }
        if (path === '/api/v1/projects/project-1/video' && method === 'POST') {
          projectStatus = 'uploaded';
          await route.fulfill({
            status: 201,
            json: {
              id: 'asset-1',
              projectId: 'project-1',
              assetType: 'source_video',
              originalFilename: 'customer-onboarding-recording-with-detailed-workflow.mp4',
              mimeType: 'video/mp4',
              sizeBytes: 16,
              durationSeconds: 6,
              width: 1280,
              height: 720,
              createdAt: timestamp,
            },
          });
          return;
        }
        if (path === '/api/v1/projects/project-1/video' && method === 'GET') {
          await route.fulfill({ status: 206, contentType: 'video/mp4', body: '' });
          return;
        }
        if (path === '/api/v1/projects/project-1/process' && method === 'POST') {
          projectStatus = 'review';
          await route.fulfill({
            status: 202,
            json: {
              id: 'job-1',
              projectId: 'project-1',
              status: 'completed',
              currentStage: 'completed',
              progressPercent: 100,
              message: 'Ready for review.',
              errorCode: null,
              errorMessage: null,
              startedAt: timestamp,
              completedAt: timestamp,
              createdAt: timestamp,
              version: 10,
            },
          });
          return;
        }
        if (path === '/api/v1/projects/project-1' && method === 'GET') {
          await route.fulfill({ json: project() });
          return;
        }
        if (path === '/api/v1/projects/project-1/segments' && method === 'GET') {
          await route.fulfill({ json: activeSegments });
          return;
        }
        if (/\/api\/v1\/segments\/[^/]+$/.test(path) && method === 'PATCH') {
          const requestBody = request.postDataJSON() as { reviewedText: string };
          activeSegments = activeSegments.map((item) =>
            item.id === path.split('/').at(-1)
              ? { ...item, reviewedText: requestBody.reviewedText }
              : item,
          );
          await route.fulfill({
            json: activeSegments.find((item) => item.id === path.split('/').at(-1)),
          });
          return;
        }
        if (/\/api\/v1\/segments\/[^/]+\/split$/.test(path) && method === 'POST') {
          const requestBody = request.postDataJSON() as {
            leftReviewedText: string;
            rightReviewedText: string;
            splitSeconds: number;
          };
          activeSegments = [
            segment('segment-left', 0, 0, requestBody.splitSeconds, requestBody.leftReviewedText),
            segment('segment-right', 1, requestBody.splitSeconds, 6, requestBody.rightReviewedText),
          ];
          await route.fulfill({ json: activeSegments });
          return;
        }
        if (path === '/api/v1/segments/merge' && method === 'POST') {
          const merged = segment(
            'segment-merged',
            0,
            0,
            6,
            activeSegments.map((item) => item.reviewedText).join('\n'),
          );
          activeSegments = [merged];
          await route.fulfill({ json: merged });
          return;
        }
        if (/\/api\/v1\/segments\/[^/]+\/screenshots$/.test(path) && method === 'GET') {
          await route.fulfill({
            json: [
              {
                id: 'shot-1',
                projectId: 'project-1',
                timestampSeconds: 2,
                width: 1280,
                height: 720,
                qualityScore: 0.9,
                isCandidate: true,
                role: primaryScreenshotId === 'shot-1' ? 'primary' : 'alternative',
                isUserSelected: false,
                createdAt: timestamp,
              },
              {
                id: 'shot-2',
                projectId: 'project-1',
                timestampSeconds: 4,
                width: 1280,
                height: 720,
                qualityScore: 0.85,
                isCandidate: true,
                role: primaryScreenshotId === 'shot-2' ? 'primary' : 'alternative',
                isUserSelected: primaryScreenshotId === 'shot-2',
                createdAt: timestamp,
              },
            ],
          });
          return;
        }
        if (/\/api\/v1\/segments\/[^/]+\/screenshot-from-video$/.test(path) && method === 'POST') {
          await route.fulfill({
            status: 201,
            json: {
              id: 'association-recovered',
              segmentId: activeSegments[0]?.id,
              screenshotId: 'shot-1',
              role: 'primary',
              confidence: 1,
              coverageScore: 1,
              roleRankReason: 'manual_recovery',
              isUserSelected: true,
              screenshot: { id: 'shot-1' },
              createdAt: timestamp,
            },
          });
          return;
        }
        if (/\/primary-screenshot\/shot-2$/.test(path) && method === 'PUT') {
          primaryScreenshotId = 'shot-2';
          await route.fulfill({ json: { id: 'association-2' } });
          return;
        }
        if (/\/api\/v1\/screenshots\/[^/]+\/(content|thumbnail)$/.test(path)) {
          await route.fulfill({
            contentType: 'image/svg+xml',
            body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 9"><rect width="16" height="9" fill="#3c3e55"/></svg>',
          });
          return;
        }
        if (path === '/api/v1/projects/project-1/export-estimate' && method === 'GET') {
          await route.fulfill({
            json: {
              canonicalImageCount: 1,
              batchCount: 1,
              batchImageCopyCount: 1,
              estimatedZipBytes: 8192,
              isApproximate: true,
              redundancyWarning: false,
            },
          });
          return;
        }
        if (path === '/api/v1/projects/project-1/exports' && method === 'GET') {
          await route.fulfill({ json: exportHistory });
          return;
        }
        if (path === '/api/v1/projects/project-1/exports' && method === 'POST') {
          projectStatus = 'completed';
          const completedExport = {
            id: 'export-1',
            projectId: 'project-1',
            status: 'completed',
            includeOriginalVideo: false,
            progressPercent: 100,
            currentStage: 'completed',
            errorCode: null,
            errorMessage: null,
            sizeBytes: 4096,
            sequence: 1,
            downloadName: 'export-1.zip',
            createdAt: timestamp,
            completedAt: timestamp,
          };
          exportHistory = [completedExport];
          await route.fulfill({ status: 202, json: completedExport });
          return;
        }
        if (path === '/api/v1/exports/export-1/download') {
          await route.fulfill({
            contentType: 'application/zip',
            headers: { 'Content-Disposition': 'attachment; filename="export-1.zip"' },
            body: 'deterministic-test-zip',
          });
          return;
        }
        await route.fulfill({
          status: 404,
          json: { error: { code: 'NOT_FOUND', message: 'Not found.', details: {} } },
        });
      });

      await page.goto('/projects');
      await expect(page.locator('html')).toHaveAttribute('lang', language);
      await expect(page.getByText(t.servicesReady)).toBeVisible();
      await expect(page.getByRole('heading', { name: t.noProjects })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.getByRole('main').getByRole('link', { name: t.newProject }).first().click();
      await expect(
        page
          .getByRole('navigation', { name: t.mainMenu })
          .getByRole('link', { name: t.newProject }),
      ).toHaveAttribute('aria-current', 'page');
      await expectNoHorizontalOverflow(page);
      await page.getByLabel(t.projectName).fill(projectName);
      await page.getByText(t.moreOptions, { exact: true }).click();
      await page.getByLabel(t.description).fill('Search workflow');
      await page.getByRole('button', { name: t.createProject }).click();
      await page.getByLabel(t.chooseVideo).setInputFiles({
        name: 'customer-onboarding-recording-with-detailed-workflow.mp4',
        mimeType: 'video/mp4',
        buffer: Buffer.from('deterministic-video'),
      });
      await page.getByRole('button', { name: t.uploadVideo }).click();
      await expect(page.getByRole('heading', { name: t.readyToProcess })).toBeVisible();
      await page.getByRole('button', { name: t.startProcessing }).click();
      await page.getByRole('link', { name: t.openReview }).click();
      await expectNoHorizontalOverflow(page);

      if (testInfo.project.name === 'chromium') {
        await verifyReviewLayoutMatrix(page, t);
      }

      const viewSwitcher = page.getByRole('radiogroup', { name: t.viewSwitcher });
      const usesWorkspaceTabs = (page.viewportSize()?.width ?? 1440) < 1024;
      if (usesWorkspaceTabs) {
        await expect(viewSwitcher).toBeVisible();
        await viewSwitcher.getByRole('radio', { name: t.video }).check({ force: true });
      }
      await page.getByRole('button', { name: t.recoverFrame }).click();
      await expect(page.getByText(t.frameSaved)).toBeAttached();

      if (usesWorkspaceTabs) {
        await viewSwitcher.getByRole('radio', { name: t.transcript }).check({ force: true });
      }
      const editor = page.getByLabel(t.reviewedText);
      await editor.fill('Select Search and review the results.');
      await page.getByRole('button', { name: t.saveText }).click();
      await editor.evaluate((element: HTMLTextAreaElement) => element.setSelectionRange(13, 13));
      await page.getByRole('button', { name: t.splitAtCaret }).click();
      await page.getByRole('button', { name: t.splitSegment }).click();
      await expect(page.getByText(t.twoSegments)).toBeVisible();

      const mergeCheckboxes = page.getByLabel(t.mergeCheckbox);
      await mergeCheckboxes.nth(0).check();
      await mergeCheckboxes.nth(1).check();
      await page.getByRole('button', { name: t.mergeAdjacent }).click();
      await page.getByRole('button', { name: t.mergeSegments }).click();
      await expect(page.getByText(t.oneSegment, { exact: true })).toBeVisible();

      if (usesWorkspaceTabs) {
        await viewSwitcher.getByRole('radio', { name: t.screenshot }).check({ force: true });
      }
      await page.getByRole('button', { name: t.useAsPrimary }).click();
      await expect(page.getByText(t.userSelected)).toBeVisible();
      await page.getByRole('link', { name: t.export, exact: true }).click();
      await expectNoHorizontalOverflow(page);
      await expect(page.getByText('metadata/manifest.json (schema 0.3.0)')).toBeVisible();
      await expect(page.getByText(t.batchFile)).toBeVisible();
      await page.getByRole('button', { name: t.createPackage }).click();
      const downloadLink = page.getByRole('link', { name: t.downloadPackage });
      await expect(downloadLink).toHaveAttribute('href', '/api/v1/exports/export-1/download');
      await expect(downloadLink).toHaveAttribute('download', '');
    });
  });
}

test.describe('language switch', () => {
  test.use({ locale: 'it-IT' });

  test('starts in the browser language, switches by reload and remembers the choice', async ({
    context,
    page,
  }) => {
    await context.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/v1/health') {
        await route.fulfill({
          json: { status: 'ok', version: '0.2.0', database: 'ok', redis: 'ok', worker: 'ok' },
        });
        return;
      }
      await route.fulfill({ json: { items: [], total: 0 } });
    });
    const group = page.getByRole('group', { name: /Lingua|Language/ });

    await page.goto('/projects');
    await expect(page.locator('html')).toHaveAttribute('lang', 'it');
    await expect(page.getByRole('heading', { name: 'Ancora nessun progetto' })).toBeVisible();
    await expect(group.getByRole('button', { name: 'Italiano' })).toHaveAttribute(
      'aria-current',
      'true',
    );

    await group.getByRole('button', { name: 'English' }).click();
    await expect(page.getByRole('heading', { name: 'No projects yet' })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(group.getByRole('button', { name: 'English' })).toHaveAttribute(
      'aria-current',
      'true',
    );

    await page.reload();
    await expect(page.getByRole('heading', { name: 'No projects yet' })).toBeVisible();
    await group.getByRole('button', { name: 'Italiano' }).click();
    await expect(page.getByRole('heading', { name: 'Ancora nessun progetto' })).toBeVisible();
    const noHorizontalScroll = await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    );
    expect(noHorizontalScroll).toBe(true);
  });
});
