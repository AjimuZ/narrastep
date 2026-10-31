// Accessibility and responsive verification of every screen in English and Italian (docs/UI.md, Verification):
// text and control contrast, keyboard operation, reduced motion, 320 px reflow, 200% zoom and text resize.
import { expect, test as base, type Locator, type Page } from '@playwright/test';
import { scanContrast } from './support/contrast';
import { REMOVABLE_PROJECT, useFakeApi, type FakeScenario } from './support/fake-api';

const test = base;

interface Screen {
  name: string;
  path: string;
  scenario: FakeScenario;
  /** Text that proves the screen finished loading, per interface language. */
  ready: { en: string; it: string };
}

const SCREENS: Screen[] = [
  {
    name: 'projects',
    path: '/projects',
    scenario: { status: 'review' },
    ready: { en: 'Weekly operations meeting', it: 'Weekly operations meeting' },
  },
  {
    name: 'new project',
    path: '/projects/new',
    scenario: { status: 'review' },
    ready: { en: 'Project name', it: 'Nome del progetto' },
  },
  {
    name: 'recorder',
    path: '/record',
    scenario: { status: 'review' },
    ready: { en: 'Choose screen', it: 'Scegli schermo' },
  },
  {
    name: 'processing',
    path: '/projects/project-1',
    scenario: { status: 'processing', jobStatus: 'processing' },
    ready: { en: 'Cancel processing', it: 'Annulla elaborazione' },
  },
  {
    name: 'processing failed',
    path: '/projects/project-1',
    scenario: { status: 'failed', jobStatus: 'failed' },
    ready: { en: 'Retry processing', it: 'Riprova elaborazione' },
  },
  {
    name: 'review',
    path: '/projects/project-1/review',
    scenario: { status: 'review' },
    ready: { en: 'Save text', it: 'Salva testo' },
  },
  {
    name: 'export',
    path: '/projects/project-1/export',
    scenario: { status: 'review' },
    ready: { en: 'Create package', it: 'Crea pacchetto' },
  },
  {
    name: 'export with a generated document to include',
    path: '/projects/project-1/export',
    scenario: { status: 'review', lightEnabled: true, generationExisting: true },
    ready: { en: 'Include the generated document', it: 'Includi il documento generato' },
  },
  {
    name: 'review with the Document link',
    path: '/projects/project-1/review',
    scenario: { status: 'review', lightEnabled: true },
    ready: { en: 'Save text', it: 'Salva testo' },
  },
  {
    name: 'document before the first generation',
    path: '/projects/project-1/document',
    scenario: { status: 'review', lightEnabled: true },
    ready: { en: 'Generate document', it: 'Genera documento' },
  },
  {
    name: 'document with a result',
    path: '/projects/project-1/document',
    scenario: { status: 'review', lightEnabled: true, generationExisting: true },
    // The text of the stored document is on the page only once it has been read.
    ready: { en: 'The customer opens', it: 'The customer opens' },
  },
  {
    name: 'storage',
    path: '/storage',
    scenario: { status: 'review' },
    // Not the title: the menu entry has the same word before the figures have loaded.
    ready: { en: 'Used by projects', it: 'Usato dai progetti' },
  },
  {
    name: 'settings',
    path: '/settings',
    scenario: { status: 'review' },
    // Light is installed and Quality is not, so the word appears once the models have loaded.
    ready: { en: 'Not installed', it: 'Non installato' },
  },
  {
    name: 'settings with local generation off',
    path: '/settings',
    scenario: { status: 'review', localGeneration: 'disabled' },
    ready: {
      en: 'NARRASTEP_LOCAL_GENERATION=disabled',
      it: 'NARRASTEP_LOCAL_GENERATION=disabled',
    },
  },
  {
    name: 'not found',
    path: '/does-not-exist',
    scenario: { status: 'review' },
    ready: { en: 'Page not found', it: 'Pagina non trovata' },
  },
];

/** Finds a screen by name; fails loudly so a typo cannot silently skip a check. */
function screenNamed(name: string): Screen {
  const screen = SCREENS.find((candidate) => candidate.name === name);
  if (!screen) throw new Error(`No screen named ${name}`);
  return screen;
}

const LANGUAGES = [
  { code: 'en', locale: 'en-US' },
  { code: 'it', locale: 'it-IT' },
] as const;

/** Opens a screen with its fake API and waits until its marker text is on the page. */
async function openScreen(page: Page, screen: Screen, language: 'en' | 'it'): Promise<void> {
  await page.goto(screen.path);
  await expect(page.locator('html')).toHaveAttribute('lang', language);
  await expect(page.getByText(screen.ready[language]).first()).toBeAttached();
  await page.evaluate(() => document.fonts.ready);
}

/** The button of the only Storage row whose original video can be removed. */
function removalTrigger(page: Page): Locator {
  return page.getByRole('button', {
    name: /^(Remove the original video of|Rimuovi il video originale di) /,
  });
}

/** Opens the Storage screen and its removal confirmation; returns the dialog. */
async function openRemovalDialog(page: Page, language: 'en' | 'it'): Promise<Locator> {
  await openScreen(page, screenNamed('storage'), language);
  await removalTrigger(page).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  return dialog;
}

/** Accessible names of the Settings controls the tests use, per interface language. */
const SETTINGS_NAMES = {
  en: {
    install: 'Install the Quality model',
    accept: 'Accept the license and download',
    cancel: 'Cancel',
    delete: 'Delete the Light model',
    confirmDelete: 'Delete model',
  },
  it: {
    install: 'Installa il modello Qualità',
    accept: 'Accetta la licenza e scarica',
    cancel: 'Annulla',
    delete: 'Elimina il modello Leggero',
    confirmDelete: 'Elimina modello',
  },
} as const;

/** Accessible names of the Document controls and states the tests use, per interface language. */
const DOCUMENT_NAMES = {
  en: { start: 'Generate document', cancel: 'Cancel generation', cancelled: 'Cancelled' },
  it: { start: 'Genera documento', cancel: 'Annulla generazione', cancelled: 'Annullato' },
} as const;

/** Opens Settings and the license panel of the model that is not installed; returns the panel. */
async function openLicensePanel(page: Page, language: 'en' | 'it'): Promise<Locator> {
  await openScreen(page, screenNamed('settings'), language);
  await page.getByRole('button', { name: SETTINGS_NAMES[language].install }).click();
  const panel = page.locator('.model-install');
  await expect(panel).toBeVisible();
  return panel;
}

/** Horizontal page scroll in pixels (zero when the page fits). */
async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/**
 * Interactive elements that stick out of the viewport sideways and are not inside a container that
 * scrolls horizontally; a clipped control cannot be used even when the page itself does not scroll.
 */
async function clippedControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const scrollsSideways = (element: Element): boolean => {
      for (let node = element.parentElement; node; node = node.parentElement) {
        const overflowX = getComputedStyle(node).overflowX;
        if (overflowX === 'auto' || overflowX === 'scroll') return true;
      }
      return false;
    };
    const found: string[] = [];
    for (const element of document.querySelectorAll('a[href], button, input, select, textarea')) {
      const rect = element.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) continue;
      if (rect.left < -1 || rect.right > window.innerWidth + 1) {
        if (!scrollsSideways(element)) {
          found.push(
            `${element.tagName.toLowerCase()} "${(element.textContent ?? '').trim().slice(0, 30)}"`,
          );
        }
      }
    }
    return found;
  });
}

// ---------------------------------------------------------------------------------------------
// Contrast
// ---------------------------------------------------------------------------------------------

for (const { code, locale } of LANGUAGES) {
  test.describe(`contrast in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale, viewport: { width: 1280, height: 900 } });

    for (const screen of SCREENS) {
      test(`text and controls on ${screen.name}`, async ({ context, page }) => {
        await useFakeApi(context, screen.scenario);
        await openScreen(page, screen, code);
        const scan = await scanContrast(page);
        expect(scan.checked).toBeGreaterThan(5);
        expect(scan.textFailures).toEqual([]);
        expect(scan.controlFailures).toEqual([]);
      });
    }
  });
}

for (const { code, locale } of LANGUAGES) {
  test.describe(`contrast of dialogs and states in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({
      locale,
      viewport: { width: 1280, height: 900 },
      contextOptions: { reducedMotion: 'reduce' },
    });

    test('delete confirmation, split dialog and screenshot preview', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      await openScreen(page, screenNamed('projects'), code);
      await page
        .getByRole('button', { name: code === 'en' ? 'Delete' : 'Elimina' })
        .first()
        .click();
      await expect(page.getByRole('dialog')).toBeVisible();
      expect((await scanContrast(page)).textFailures).toEqual([]);

      await openScreen(page, screenNamed('review'), code);
      await page
        .getByRole('button', { name: code === 'en' ? 'Split at caret' : 'Dividi al cursore' })
        .first()
        .click();
      await expect(page.getByRole('dialog')).toBeVisible();
      expect((await scanContrast(page)).textFailures).toEqual([]);
      expect((await scanContrast(page)).controlFailures).toEqual([]);
      await page.keyboard.press('Escape');

      await page
        .getByRole('button', { name: /^(Open full-size primary|Apri lo screenshot principale)/ })
        .first()
        .click();
      await expect(page.getByRole('dialog')).toBeVisible();
      expect((await scanContrast(page)).textFailures).toEqual([]);
    });

    test('storage removal confirmation and the notice after it', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      const dialog = await openRemovalDialog(page, code);
      const inDialog = await scanContrast(page);
      expect(inDialog.textFailures).toEqual([]);
      expect(inDialog.controlFailures).toEqual([]);

      await dialog
        .getByRole('button', {
          name: code === 'en' ? 'Remove original video' : 'Rimuovi video originale',
        })
        .click();
      await expect(page.getByRole('status').filter({ hasText: REMOVABLE_PROJECT })).toBeVisible();
      const afterwards = await scanContrast(page);
      expect(afterwards.textFailures).toEqual([]);
      expect(afterwards.controlFailures).toEqual([]);
    });

    test('settings license panel, download progress and delete confirmation', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      const names = SETTINGS_NAMES[code];
      const panel = await openLicensePanel(page, code);
      const withPanel = await scanContrast(page);
      expect(withPanel.textFailures).toEqual([]);
      expect(withPanel.controlFailures).toEqual([]);

      await panel.getByRole('button', { name: names.accept }).click();
      const gauge = page.locator('.model-progress [role="img"]');
      await expect(gauge).toBeVisible();
      await expect(gauge).toHaveAttribute('aria-label', /40%/);
      const downloading = await scanContrast(page);
      expect(downloading.textFailures).toEqual([]);
      expect(downloading.controlFailures).toEqual([]);

      await page.getByRole('button', { name: names.delete }).click();
      await expect(page.getByRole('dialog')).toBeVisible();
      const confirming = await scanContrast(page);
      expect(confirming.textFailures).toEqual([]);
      expect(confirming.controlFailures).toEqual([]);
    });

    test('document progress, cancelled and failed generations keep their contrast', async ({
      context,
      page,
    }) => {
      const names = DOCUMENT_NAMES[code];
      for (const run of ['stalled', 'failing'] as const) {
        await useFakeApi(context, {
          status: 'review',
          lightEnabled: true,
          generationRun: run,
        });
        await openScreen(page, screenNamed('document before the first generation'), code);
        await page.getByRole('button', { name: names.start }).click();
        if (run === 'stalled') {
          const gauge = page.locator('.progress [role="img"]');
          await expect(gauge).toHaveAttribute('aria-label', /33%/, { timeout: 15_000 });
          const progress = await scanContrast(page);
          expect(progress.textFailures).toEqual([]);
          expect(progress.controlFailures).toEqual([]);

          await page.getByRole('button', { name: names.cancel }).click();
          await expect(page.locator('.status-head .ns-pill')).toHaveText(names.cancelled);
          const cancelled = await scanContrast(page);
          expect(cancelled.textFailures).toEqual([]);
          expect(cancelled.controlFailures).toEqual([]);
        } else {
          await expect(page.locator('.failure')).toBeVisible({ timeout: 15_000 });
          const failed = await scanContrast(page);
          expect(failed.textFailures).toEqual([]);
          expect(failed.controlFailures).toEqual([]);
        }
      }
    });

    test('hovered buttons, rows and menu entries keep their contrast', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      for (const screen of [
        screenNamed('projects'),
        screenNamed('review'),
        screenNamed('export'),
        screenNamed('storage'),
        screenNamed('settings'),
      ]) {
        await openScreen(page, screen, code);
        const targets = page.locator(
          'main button:not([disabled]), main a.ns-button, .ns-row, nav a',
        );
        const count = Math.min(await targets.count(), 14);
        for (let index = 0; index < count; index += 1) {
          const target = targets.nth(index);
          if (!(await target.isVisible())) continue;
          await target.hover();
          const scan = await scanContrast(page);
          expect(scan.textFailures, `hovering item ${index} on ${screen.name}`).toEqual([]);
        }
      }
    });
  });
}

// ---------------------------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------------------------

/** One stop of the Tab sequence. */
interface TabStop {
  label: string;
  name: string;
  /** Visible box of the element, or of its label when the element itself is visually hidden. */
  box: { x: number; y: number; width: number; height: number };
  obscured: boolean;
}

/** Waits for every finite animation and transition on the page, then one more frame. */
async function settleAnimations(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    );
    await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  });
}

/** Describes the focused element: a short label, its accessible name, the box to look at. */
async function describeFocus(page: Page): Promise<TabStop | null> {
  // Focus-driven style changes (such as the skip link sliding in) finish before the box is measured.
  await settleAnimations(page);
  return page.evaluate((): TabStop | null => {
    const element = document.activeElement as HTMLElement | null;
    if (!element || element === document.body) return null;
    const labelled = element.getAttribute('aria-labelledby');
    const labels =
      'labels' in element ? Array.from((element as HTMLInputElement).labels ?? []) : [];
    const name = (
      element.getAttribute('aria-label') ||
      (labelled
        ? labelled
            .split(' ')
            .map((id) => document.getElementById(id)?.textContent ?? '')
            .join(' ')
        : '') ||
      labels.map((label) => label.textContent ?? '').join(' ') ||
      element.textContent ||
      element.getAttribute('title') ||
      (element as HTMLInputElement).placeholder ||
      ''
    )
      .replace(/\s+/g, ' ')
      .trim();
    let target: HTMLElement = element;
    let rect = target.getBoundingClientRect();
    // A radio, checkbox or file input shows its focus on the label or card that wraps it.
    const wrapped =
      element instanceof HTMLInputElement && ['radio', 'checkbox', 'file'].includes(element.type);
    if (wrapped || rect.width < 8 || rect.height < 8) {
      target = (element.closest('label, .type-card') as HTMLElement | null) ?? element;
      rect = target.getBoundingClientRect();
    }
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    const obscured =
      !hit || !(target.contains(hit) || hit.contains(target) || element.contains(hit));
    return {
      label: `${element.tagName.toLowerCase()}${element.id ? '#' + element.id : ''}`,
      name,
      box: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      obscured,
    };
  });
}

/**
 * Presses Tab through the whole page and returns every stop. Stops when the focus leaves the page
 * (back to the browser) or returns to the first stop, and fails when it never ends (keyboard trap).
 */
async function tabThrough(page: Page, requireVisibleFocus: boolean): Promise<TabStop[]> {
  const stops: TabStop[] = [];
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  for (let press = 0; press < 120; press += 1) {
    await page.keyboard.press('Tab');
    const stop = await describeFocus(page);
    if (!stop) return stops;
    const first = stops[0];
    if (first && first.label === stop.label && first.name === stop.name) return stops;
    if (requireVisibleFocus && stop.box.width >= 1) {
      const clip = {
        x: Math.max(0, stop.box.x - 6),
        y: Math.max(0, stop.box.y - 6),
        width: Math.min(
          stop.box.width + 12,
          (page.viewportSize()?.width ?? 1280) - Math.max(0, stop.box.x - 6),
        ),
        height: Math.min(
          stop.box.height + 12,
          (page.viewportSize()?.height ?? 900) - Math.max(0, stop.box.y - 6),
        ),
      };
      if (clip.width > 0 && clip.height > 0) {
        const focused = await page.screenshot({ clip });
        await page.evaluate(() => (document.activeElement as HTMLElement).blur());
        const blurred = await page.screenshot({ clip });
        expect(
          focused.equals(blurred),
          `focus on <${stop.label}> "${stop.name}" shows nothing on screen`,
        ).toBe(false);
        await page.evaluate(() => undefined);
        // Back to the same element so the next Tab continues from it.
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
      }
    }
    stops.push(stop);
  }
  throw new Error(`Tab never leaves the page: keyboard trap after ${stops.length} stops`);
}

/** Every visible control that must be reachable with Tab (one radio per group, as browsers do). */
async function reachableControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const seenGroups = new Set<string>();
    const found: string[] = [];
    const selector =
      'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, video[controls], [tabindex]:not([tabindex="-1"])';
    for (const element of document.querySelectorAll<HTMLElement>(selector)) {
      // `checkVisibility` is false inside `display: none` parents and closed <details> blocks.
      if (!element.checkVisibility({ visibilityProperty: true }) || element.closest('[inert]'))
        continue;
      const rect = element.getBoundingClientRect();
      const visuallyHidden = rect.width < 2 || rect.height < 2;
      if (visuallyHidden && !element.matches('input')) continue;
      if (element instanceof HTMLInputElement && element.type === 'radio') {
        // Tab enters a radio group once, on the checked radio or else on the first one.
        const group = Array.from(
          document.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${element.name}"]`),
        );
        const entry = group.find((radio) => radio.checked) ?? group[0];
        if (seenGroups.has(element.name) || element !== entry) continue;
        seenGroups.add(element.name);
      }
      found.push(
        `${element.tagName.toLowerCase()}${element.id ? '#' + element.id : ''}|${(element.getAttribute('aria-label') ?? element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)}`,
      );
    }
    return found;
  });
}

for (const { code, locale } of LANGUAGES) {
  test.describe(`keyboard in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({
      locale,
      viewport: { width: 1280, height: 900 },
      contextOptions: { reducedMotion: 'reduce' },
    });

    for (const screen of SCREENS) {
      test(`tab order and visible focus on ${screen.name}`, async ({ context, page }) => {
        await useFakeApi(context, screen.scenario);
        await openScreen(page, screen, code);
        const expected = await reachableControls(page);
        const stops = await tabThrough(page, true);

        expect(stops.length).toBeGreaterThan(2);
        for (const stop of stops) {
          expect(stop.name, `<${stop.label}> has no accessible name`).not.toBe('');
          expect(stop.obscured, `<${stop.label}> "${stop.name}" is covered when focused`).toBe(
            false,
          );
        }
        // The menu comes first: its links are the first stops on every screen.
        expect(stops[0]?.label).toMatch(/^(a|button)/);
        // Every control found by query is on the Tab path, once and in the same order.
        expect(stops.map((stop) => stop.label)).toEqual(
          expected.map((entry) => entry.split('|')[0]),
        );
      });
    }

    test('delete confirmation keeps focus inside and returns it', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      await openScreen(page, screenNamed('projects'), code);
      const trigger = page
        .getByRole('button', { name: code === 'en' ? 'Delete' : 'Elimina' })
        .first();
      await trigger.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveAttribute('aria-modal', 'true');
      // Focus starts inside the dialog and never leaves it, forwards or backwards.
      for (let press = 0; press < 6; press += 1) {
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
        await page.keyboard.press('Tab');
      }
      for (let press = 0; press < 3; press += 1) {
        await page.keyboard.press('Shift+Tab');
        expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
      }
      await dialog.getByRole('button', { name: code === 'en' ? 'Cancel' : 'Annulla' }).focus();
      await page.keyboard.press('Enter');
      await expect(dialog).toBeHidden();
      await expect(trigger).toBeFocused();
    });

    test('storage removal confirmation keeps focus inside, returns it and closes with Escape', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      await openScreen(page, screenNamed('storage'), code);
      const trigger = removalTrigger(page);
      await trigger.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveAttribute('aria-modal', 'true');
      for (let press = 0; press < 6; press += 1) {
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

      await page.keyboard.press('Enter');
      await expect(dialog).toBeVisible();
      await dialog.getByRole('button', { name: code === 'en' ? 'Cancel' : 'Annulla' }).focus();
      await page.keyboard.press('Enter');
      await expect(dialog).toBeHidden();
      await expect(trigger).toBeFocused();
    });

    test('settings license panel opens and closes with the keyboard and keeps focus on the model', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      const names = SETTINGS_NAMES[code];
      await openScreen(page, screenNamed('settings'), code);
      const trigger = page.getByRole('button', { name: names.install });
      await trigger.focus();
      await page.keyboard.press('Enter');
      const panel = page.locator('.model-install');
      await expect(panel).toBeVisible();
      await expect(panel.getByRole('link')).toHaveAttribute('href', /gemma_4_license/);

      await panel.getByRole('button', { name: names.cancel }).focus();
      await page.keyboard.press('Enter');
      await expect(panel).toBeHidden();
      // The button that had the focus is gone, so the model name takes it: the keyboard user is not
      // sent back to the top of the page.
      await expect(page.locator('#model-title-gemma-4-e4b')).toBeFocused();

      await page.getByRole('button', { name: names.delete }).focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole('button', { name: names.cancel })).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(page.getByRole('button', { name: names.delete })).toBeFocused();
    });

    test('document generation starts with the keyboard and keeps focus on the status', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review', lightEnabled: true, generationRun: 'stalled' });
      const names = DOCUMENT_NAMES[code];
      await openScreen(page, screenNamed('document before the first generation'), code);
      const start = page.getByRole('button', { name: names.start });
      await start.focus();
      await page.keyboard.press('Enter');

      // The button that had the focus is replaced by Cancel, so the status heading takes it.
      await expect(page.locator('#generation-status')).toBeFocused();
      const cancel = page.getByRole('button', { name: names.cancel });
      await cancel.focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('.status-head .ns-pill')).toHaveText(names.cancelled);
      await expect(page.locator('#generation-status')).toBeFocused();
    });

    test('split dialog and screenshot preview close with Escape', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      await openScreen(page, screenNamed('review'), code);
      const split = page
        .getByRole('button', { name: code === 'en' ? 'Split at caret' : 'Dividi al cursore' })
        .first();
      await split.focus();
      await page.keyboard.press('Enter');
      const splitDialog = page.getByRole('dialog');
      await expect(splitDialog).toBeVisible();
      expect(await splitDialog.evaluate((node) => node.contains(document.activeElement))).toBe(
        true,
      );
      await page.keyboard.press('Escape');
      await expect(splitDialog).toBeHidden();
      await expect(split).toBeFocused();
    });
  });
}

for (const { code, locale } of LANGUAGES) {
  test.describe(`radio groups in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({
      locale,
      viewport: { width: 390, height: 844 },
      contextOptions: { reducedMotion: 'reduce' },
    });

    test('project types and review views move with the arrow keys', async ({ context, page }) => {
      await useFakeApi(context, { status: 'review' });
      await openScreen(page, screenNamed('new project'), code);
      const types = page.getByRole('radio');
      const [first, second] = [types.nth(0), types.nth(1)];
      await first.focus();
      await page.keyboard.press('ArrowDown');
      await expect(second).toBeFocused();
      await expect(second).toBeChecked();
      await expect(first).not.toBeChecked();

      await openScreen(page, screenNamed('review'), code);
      const views = page.getByRole('radiogroup').getByRole('radio');
      await expect(views).toHaveCount(3);
      await views.nth(1).focus();
      await page.keyboard.press('ArrowRight');
      await expect(views.nth(2)).toBeChecked();
      await expect(page.locator('#review-screenshot-panel')).toBeVisible();
    });
  });
}

// ---------------------------------------------------------------------------------------------
// Reduced motion
// ---------------------------------------------------------------------------------------------

/** Elements (and pseudo-elements) that still run an animation or a transition longer than a frame. */
async function movingElements(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const longest = (value: string) =>
      Math.max(
        0,
        ...value
          .split(',')
          .map((part) => parseFloat(part) * (part.trim().endsWith('ms') ? 1 : 1000)),
      );
    const moving: string[] = [];
    for (const element of document.querySelectorAll('*')) {
      for (const pseudo of [null, '::before', '::after']) {
        const style = getComputedStyle(element, pseudo);
        const animated =
          style.animationName !== 'none' &&
          longest(style.animationDuration) > 1 &&
          style.animationPlayState === 'running';
        const transitioned =
          longest(style.transitionDuration) > 1 && style.transitionProperty !== 'none';
        if (animated || transitioned) {
          moving.push(`${element.tagName.toLowerCase()}.${element.className}${pseudo ?? ''}`);
        }
      }
    }
    return moving;
  });
}

for (const { code, locale } of LANGUAGES) {
  test.describe(`reduced motion in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale, viewport: { width: 1280, height: 900 } });

    for (const screen of SCREENS) {
      test(`${screen.name} has no motion when the system asks for none`, async ({
        context,
        page,
      }) => {
        await useFakeApi(context, screen.scenario);
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await openScreen(page, screen, code);
        // Hover a button so its transition would run if it were allowed.
        await page.locator('button, a.ns-button').first().hover();
        expect(await movingElements(page)).toEqual([]);
      });
    }

    test('storage removal confirmation has no motion when the system asks for none', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, { status: 'review' });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const dialog = await openRemovalDialog(page, code);
      await dialog.getByRole('button').first().hover();
      expect(await movingElements(page)).toEqual([]);
    });

    test('the same processing screen does animate without the preference (the check can fail)', async ({
      context,
      page,
    }) => {
      await useFakeApi(context, screenNamed('processing').scenario);
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await openScreen(page, screenNamed('processing'), code);
      expect((await movingElements(page)).length).toBeGreaterThan(0);
    });
  });
}

// ---------------------------------------------------------------------------------------------
// Reflow, 200% zoom, text resize and target size
// ---------------------------------------------------------------------------------------------

/**
 * Visible text that an `overflow: hidden` ancestor cuts off (WCAG 1.4.4 and 1.4.10): a box whose
 * content is taller or wider than the box itself. Ellipsis truncation of a single line is allowed
 * because the full text stays available to assistive technology and by title or wrap elsewhere.
 */
async function clippedText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>('body *')) {
      const style = getComputedStyle(element);
      if (
        !['hidden', 'clip'].includes(style.overflowY) &&
        !['hidden', 'clip'].includes(style.overflowX)
      )
        continue;
      if (element.closest('video, .cdk-visually-hidden, .visually-hidden')) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2 || !(element.textContent ?? '').trim()) continue;
      const cutVertically =
        ['hidden', 'clip'].includes(style.overflowY) &&
        element.scrollHeight > element.clientHeight + 2;
      const cutSideways =
        ['hidden', 'clip'].includes(style.overflowX) &&
        element.scrollWidth > element.clientWidth + 2 &&
        style.textOverflow !== 'ellipsis';
      if (cutVertically || cutSideways) {
        found.push(
          `<${element.tagName.toLowerCase()} class="${element.className}"> "${(element.textContent ?? '').trim().slice(0, 30)}"`,
        );
      }
    }
    return found;
  });
}

/** Interactive elements smaller than 24 × 24 CSS px (WCAG 2.5.8), counting the wrapping label. */
async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, summary',
    )) {
      if (!element.checkVisibility({ visibilityProperty: true })) continue;
      // Inline links inside a sentence are exempt; so are controls whose wrapping label is the target.
      if (element.tagName === 'A' && getComputedStyle(element).display === 'inline') continue;
      const box = (element.closest('label') ?? element).getBoundingClientRect();
      if (box.width < 24 || box.height < 24) {
        found.push(
          `${element.tagName.toLowerCase()}${element.id ? '#' + element.id : ''} ${Math.round(box.width)}×${Math.round(box.height)} "${(element.getAttribute('aria-label') ?? element.textContent ?? '').trim().slice(0, 24)}"`,
        );
      }
    }
    return found;
  });
}

/** Viewports that stand for the supported widths, 200% zoom and 400% zoom of a 1280 px window. */
const REFLOW_VIEWPORTS = [
  { label: '320 px', width: 320, height: 640, scale: 1 },
  { label: '390 px', width: 390, height: 844, scale: 1 },
  { label: '768 px', width: 768, height: 900, scale: 1 },
  { label: '1024 px', width: 1024, height: 900, scale: 1 },
  { label: '1440 px', width: 1440, height: 900, scale: 1 },
  // Browser zoom of a 1280 × 720 window: the page sees half (200%) or a quarter (400%) of the CSS pixels.
  { label: '200% zoom of 1280 px', width: 640, height: 360, scale: 2 },
  { label: '400% zoom of 1280 px', width: 320, height: 180, scale: 4 },
];

for (const { code, locale } of LANGUAGES) {
  for (const viewport of REFLOW_VIEWPORTS) {
    test.describe(`reflow at ${viewport.label} in ${code === 'en' ? 'English' : 'Italian'}`, () => {
      test.use({
        locale,
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.scale,
        contextOptions: { reducedMotion: 'reduce' },
      });

      for (const screen of SCREENS) {
        test(`${screen.name} fits without horizontal scrolling`, async ({ context, page }) => {
          await useFakeApi(context, screen.scenario);
          await openScreen(page, screen, code);
          expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
          expect(await clippedControls(page)).toEqual([]);
          expect(await clippedText(page)).toEqual([]);
          if (viewport.width <= 390) {
            expect(await smallTargets(page)).toEqual([]);
          }
        });
      }

      test('document progress fits and keeps Cancel reachable', async ({ context, page }) => {
        await useFakeApi(context, {
          status: 'review',
          lightEnabled: true,
          generationRun: 'stalled',
        });
        await openScreen(page, screenNamed('document before the first generation'), code);
        await page.getByRole('button', { name: DOCUMENT_NAMES[code].start }).click();
        await expect(page.locator('.progress [role="img"]')).toBeVisible({ timeout: 15_000 });
        expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
        expect(await clippedControls(page)).toEqual([]);
        expect(await clippedText(page)).toEqual([]);
        if (viewport.width <= 390) {
          expect(await smallTargets(page)).toEqual([]);
        }
        const cancel = page.getByRole('button', { name: DOCUMENT_NAMES[code].cancel });
        await cancel.scrollIntoViewIfNeeded();
        await expect(cancel).toBeInViewport();
      });

      test('settings license panel fits and keeps its actions reachable', async ({
        context,
        page,
      }) => {
        await useFakeApi(context, { status: 'review' });
        const panel = await openLicensePanel(page, code);
        expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
        expect(await clippedControls(page)).toEqual([]);
        expect(await clippedText(page)).toEqual([]);
        if (viewport.width <= 390) {
          expect(await smallTargets(page)).toEqual([]);
        }
        for (const action of await panel.getByRole('button').all()) {
          await action.scrollIntoViewIfNeeded();
          await expect(action).toBeInViewport();
        }
      });

      test('storage removal confirmation fits and keeps its actions reachable', async ({
        context,
        page,
      }) => {
        await useFakeApi(context, { status: 'review' });
        const dialog = await openRemovalDialog(page, code);
        const box = await dialog.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(-1);
        expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
        expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
        expect(await clippedControls(page)).toEqual([]);
        expect(await clippedText(page)).toEqual([]);
        if (viewport.width <= 390) {
          expect(await smallTargets(page)).toEqual([]);
        }
        // The dialog scrolls inside itself when it is taller than the window, as at 400% zoom.
        for (const action of await dialog.getByRole('button').all()) {
          await action.scrollIntoViewIfNeeded();
          await expect(action).toBeInViewport();
        }
      });
    });
  }

  test.describe(`breakpoints follow the browser font size in ${code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale });

    // A browser font size of 200% only moves the layout when media and container queries use rem:
    // then 1280 px at 32 px per rem lays out like the 640 px viewport above (the 200% zoom case).
    // Headless Chromium here ignores the default-font-size preference, so this guard stands in for it.
    test('every media and container query is written in rem', async ({ context, page }) => {
      await useFakeApi(context, screenNamed('projects').scenario);
      await openScreen(page, screenNamed('projects'), code);
      const pixelQueries = await page.evaluate(() => {
        const found: string[] = [];
        const visit = (rules: CSSRuleList) => {
          for (const rule of Array.from(rules)) {
            const condition =
              (rule as CSSMediaRule).conditionText ??
              (rule as CSSContainerRule).containerQuery ??
              '';
            if (/\d\s*px/.test(condition)) found.push(condition);
            if ('cssRules' in rule) visit((rule as CSSGroupingRule).cssRules);
          }
        };
        for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
        return found;
      });
      expect(pixelQueries).toEqual([]);
    });
  });
}
