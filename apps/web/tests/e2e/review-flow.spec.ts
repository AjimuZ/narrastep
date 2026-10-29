import { expect, test, type Page } from '@playwright/test';

const timestamp = '2026-07-15T10:00:00Z';
const projectName = 'Contract search and customer onboarding workflow for operations';

/** Confirms that the current route never creates horizontal document scrolling. */
async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflowPixels = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflowPixels).toBeLessThanOrEqual(1);
}

/** Exercises the three responsive review arrangements at the supported viewport boundaries. */
async function verifyReviewLayoutMatrix(page: Page): Promise<void> {
  const viewports = [
    { width: 320, height: 720 },
    { width: 390, height: 844 },
    { width: 768, height: 900 },
    { width: 1024, height: 900 },
    { width: 1280, height: 900 },
    { width: 1440, height: 900 },
  ];
  const switcher = page.getByRole('radiogroup', { name: 'Review workspace view' });
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
      await switcher.getByRole('radio', { name: 'Screenshot' }).check({ force: true });
      await expect(transcriptPanel).toBeVisible();
      await expect(structurePanel).toBeVisible();
      await expect(videoPanel).toBeHidden();
      await expect(screenshotPanel).toBeVisible();
      await switcher.getByRole('radio', { name: 'Video' }).check({ force: true });
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

test('creates, reviews, restructures, reassigns evidence, and exports a project', async ({
  context,
  page,
}, testInfo) => {
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
        headers: { 'Content-Disposition': 'attachment; filename="narrastep-export.zip"' },
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
  await expectNoHorizontalOverflow(page);
  await page.getByRole('link', { name: 'Create your first project' }).click();
  await expectNoHorizontalOverflow(page);
  await page.getByLabel('Project name').fill(projectName);
  await page.getByText('More options', { exact: true }).click();
  await page.getByLabel('Short description (optional)').fill('Search workflow');
  await page.getByRole('button', { name: 'Continue to video' }).click();
  await page.getByLabel('Choose a file').setInputFiles({
    name: 'customer-onboarding-recording-with-detailed-workflow.mp4',
    mimeType: 'video/mp4',
    buffer: Buffer.from('deterministic-video'),
  });
  await page.getByRole('button', { name: 'Upload and validate' }).click();
  await expect(page.getByRole('heading', { name: /Ready to process/ })).toBeVisible();
  await page.getByRole('button', { name: 'Start local processing' }).click();
  await page.getByRole('link', { name: 'Open review workspace' }).click();
  await expectNoHorizontalOverflow(page);

  if (testInfo.project.name === 'chromium') {
    await verifyReviewLayoutMatrix(page);
  }

  const viewSwitcher = page.getByRole('radiogroup', { name: 'Review workspace view' });
  const usesWorkspaceTabs = (page.viewportSize()?.width ?? 1440) < 1024;
  if (usesWorkspaceTabs) {
    await expect(viewSwitcher).toBeVisible();
    await viewSwitcher.getByRole('radio', { name: 'Video' }).check({ force: true });
  }
  await page.getByRole('button', { name: 'Recover current frame' }).click();
  await expect(page.getByText('Current video frame saved as primary evidence.')).toBeAttached();

  if (usesWorkspaceTabs) {
    await viewSwitcher.getByRole('radio', { name: 'Transcript' }).check({ force: true });
  }
  const editor = page.getByLabel('Reviewed text for segment 1');
  await editor.fill('Select Search and review the results.');
  await page.getByRole('button', { name: 'Save text' }).click();
  await editor.evaluate((element: HTMLTextAreaElement) => element.setSelectionRange(13, 13));
  await page.getByRole('button', { name: 'Split at caret' }).click();
  await page.getByRole('button', { name: 'Split segment' }).click();
  await expect(page.getByText('2 segments')).toBeVisible();

  const mergeCheckboxes = page.getByLabel(/^Merge segment \d+$/);
  await mergeCheckboxes.nth(0).check();
  await mergeCheckboxes.nth(1).check();
  await page.getByRole('button', { name: 'Merge adjacent' }).click();
  await page.getByRole('button', { name: 'Merge segments' }).click();
  await expect(page.getByText('1 segments')).toBeVisible();

  if (usesWorkspaceTabs) {
    await viewSwitcher.getByRole('radio', { name: 'Screenshot' }).check({ force: true });
  }
  await page.getByRole('button', { name: 'Use as primary' }).click();
  await expect(page.getByText('User selected')).toBeVisible();
  await page.getByRole('link', { name: 'Export project' }).click();
  await expectNoHorizontalOverflow(page);
  await expect(page.getByText('manifest.json (schema 0.2.0)')).toBeVisible();
  await expect(
    page.getByText('chatgpt-batches/batch-NNN/batch-input.md + up to 19 images'),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Generate ZIP export' }).click();
  const downloadLink = page.getByRole('link', { name: 'Download ZIP' });
  await expect(downloadLink).toHaveAttribute('href', '/api/v1/exports/export-1/download');
  await expect(downloadLink).toHaveAttribute('download', '');
});
