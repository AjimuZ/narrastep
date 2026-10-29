// Contrast scan of a rendered page: reads painted colors in the browser and applies WCAG 2.2 maths in Node.
import type { Page } from '@playwright/test';

/** Straight (non-premultiplied) sRGB color with channels 0–255 and alpha 0–1. */
type Rgba = [number, number, number, number];

/** What the browser reports for one text run or placeholder. */
interface PaintedText {
  label: string;
  color: Rgba;
  /** Backgrounds from the outermost element down to the text's own box, until one is opaque. */
  layers: Rgba[];
  /** Product of the opacity of the element and all its ancestors. */
  opacity: number;
  fontSize: number;
  bold: boolean;
  /** True when a background image sits behind the text, so its color cannot be measured. */
  onImage: boolean;
  /** Placeholder text is exempt from the large-text allowance only by size; kept for reporting. */
  placeholder: boolean;
}

/** What the browser reports for one field frame. */
interface PaintedField {
  label: string;
  border: Rgba | null;
  fill: Rgba;
  around: Rgba[];
}

/** Everything the scan needs from the page in one round trip. */
interface PaintedPage {
  pageBackground: Rgba;
  texts: PaintedText[];
  fields: PaintedField[];
  /** Token colors used for pairs that depend on state rather than one element. */
  tokens: Record<string, Rgba>;
}

/** Result of one scan: how much it measured and every pair that fell short. */
export interface ContrastScan {
  checked: number;
  skippedOnImage: number;
  textFailures: string[];
  controlFailures: string[];
}

/** Surfaces a field or focus ring can sit on (docs/UI.md tokens). */
const SURFACE_TOKENS = [
  '--color-bg',
  '--color-bg-deep',
  '--color-bg-elevated',
  '--color-bg-panel',
  '--color-bg-raised',
  '--color-surface-hover',
];

/** Composites `top` over an opaque or translucent `bottom`. */
function over(top: Rgba, bottom: Rgba): Rgba {
  const alpha = top[3] + bottom[3] * (1 - top[3]);
  if (alpha === 0) return [0, 0, 0, 0];
  const mix = (i: 0 | 1 | 2) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / alpha;
  return [mix(0), mix(1), mix(2), alpha];
}

/** Flattens backgrounds listed from the outermost layer inwards over the page background. */
function flatten(layers: Rgba[], pageBackground: Rgba): Rgba {
  return layers.reduce((below, layer) => over(layer, below), pageBackground);
}

/** WCAG relative luminance of an opaque color. */
function luminance([r, g, b]: Rgba): number {
  const channel = (value: number) => {
    const scaled = value / 255;
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio between two opaque colors. */
export function contrastRatio(a: Rgba, b: Rgba): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * Reads the painted colors of the visible page. Runs in the browser, so it only collects data: any
 * CSS color (rgb, color-mix, color()) is normalized through a canvas pixel, and disabled controls,
 * hidden text and `<option>` text are left out.
 */
function collectPaint(): PaintedPage {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('Canvas is not available');
  const parse = (css: string): Rgba => {
    const direct = /^rgba?\(([^)]+)\)$/.exec(css);
    if (direct?.[1]) {
      const parts = direct[1]
        .split(/[\s,/]+/)
        .filter(Boolean)
        .map(Number);
      return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1];
    }
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = '#000';
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0, a = 0] = context.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const root = getComputedStyle(document.documentElement);
  const token = (name: string) => parse(root.getPropertyValue(name).trim());

  /** Background layers behind an element, outermost first; `onImage` when an image is painted. */
  const backdrop = (element: Element | null): { layers: Rgba[]; onImage: boolean } => {
    const layers: Rgba[] = [];
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== 'none') return { layers, onImage: true };
      const color = parse(style.backgroundColor);
      if (color[3] > 0) layers.unshift(color);
      if (color[3] === 1) break;
    }
    return { layers, onImage: false };
  };
  const opacityOf = (element: Element): number => {
    let value = 1;
    for (let node: Element | null = element; node; node = node.parentElement) {
      value *= Number(getComputedStyle(node).opacity);
    }
    return value;
  };
  const describe = (element: Element) =>
    `<${element.tagName.toLowerCase()}${element.className ? ` class="${element.className}"` : ''}>`;

  const texts: PaintedText[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const element = node.parentElement;
    const words = node.textContent?.trim();
    if (!element || !words || seen.has(element)) continue;
    if (['SCRIPT', 'STYLE', 'OPTION', 'TITLE'].includes(element.tagName)) continue;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (style.visibility !== 'visible' || style.display === 'none') continue;
    if (rect.width < 2 || rect.height < 2) continue;
    if (element.closest('[disabled], [aria-disabled="true"], fieldset:disabled')) continue;
    seen.add(element);
    texts.push({
      label: `"${words.slice(0, 40)}" in ${describe(element)}`,
      color: parse(style.color),
      ...backdrop(element),
      opacity: opacityOf(element),
      fontSize: parseFloat(style.fontSize),
      bold: Number(style.fontWeight) >= 700,
      placeholder: false,
    });
  }

  // Placeholder text is read from the ::placeholder box and painted on the field background.
  for (const field of document.querySelectorAll('input[placeholder], textarea[placeholder]')) {
    if (field.getBoundingClientRect().width < 2) continue;
    const style = getComputedStyle(field, '::placeholder');
    texts.push({
      label: `placeholder of ${describe(field)}`,
      color: parse(style.color),
      ...backdrop(field),
      opacity: Number(style.opacity),
      fontSize: parseFloat(style.fontSize),
      bold: false,
      placeholder: true,
    });
  }

  // Field frames: text inputs, selects, textareas and radios or checkboxes the stylesheet draws itself
  // (`appearance: none`). Sliders, native checkboxes and file inputs are drawn by the browser or by a
  // wrapping label and are covered by the focus and target checks instead.
  const fields: PaintedField[] = [];
  const fieldSelector =
    'input:not([type=file]):not([type=hidden]):not([type=range]), select, textarea';
  for (const field of document.querySelectorAll(fieldSelector)) {
    const rect = field.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) continue;
    const style = getComputedStyle(field);
    const choice = field instanceof HTMLInputElement && ['radio', 'checkbox'].includes(field.type);
    if (choice && style.appearance !== 'none') continue;
    const hasBorder = parseFloat(style.borderTopWidth) > 0 && style.borderTopStyle !== 'none';
    fields.push({
      label: `${describe(field)} ${field.id || field.getAttribute('name') || ''}`.trim(),
      border: hasBorder ? parse(style.borderTopColor) : null,
      fill: parse(style.backgroundColor),
      around: backdrop(field.parentElement).layers,
    });
  }

  const tokens: Record<string, Rgba> = {
    '--color-focus-ring': token('--color-focus-ring'),
  };
  for (const name of [
    '--color-bg',
    ...[
      '--color-bg-deep',
      '--color-bg-elevated',
      '--color-bg-panel',
      '--color-bg-raised',
      '--color-surface-hover',
    ],
  ]) {
    tokens[name] = token(name);
  }
  return { pageBackground: token('--color-bg'), texts, fields, tokens };
}

/**
 * Measures text contrast (WCAG 1.4.3: 4.5:1, or 3:1 for text of 24 px or 18.66 px bold) and the
 * non-text contrast of field frames and the focus ring (WCAG 1.4.11: 3:1) on the current page.
 * Text on a background image cannot be measured by color and is counted in `skippedOnImage`.
 */
export async function scanContrast(page: Page): Promise<ContrastScan> {
  // A fade-in that is still running would lower the measured opacity, so finite animations finish first.
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    ),
  );
  const painted = await page.evaluate(collectPaint);
  const scan: ContrastScan = {
    checked: 0,
    skippedOnImage: 0,
    textFailures: [],
    controlFailures: [],
  };

  for (const text of painted.texts) {
    if (text.onImage) {
      scan.skippedOnImage += 1;
      continue;
    }
    const behind = flatten(text.layers, painted.pageBackground);
    const faded: Rgba = [text.color[0], text.color[1], text.color[2], text.color[3] * text.opacity];
    const large = text.fontSize >= 24 || (text.bold && text.fontSize >= 18.66);
    const needed = large ? 3 : 4.5;
    const actual = contrastRatio(over(faded, behind), behind);
    scan.checked += 1;
    if (actual < needed)
      scan.textFailures.push(`${actual.toFixed(2)}:1 < ${needed}:1 ${text.label}`);
  }

  for (const field of painted.fields) {
    const around = flatten(field.around, painted.pageBackground);
    const fill = over(field.fill, around);
    const frame = field.border ? over(field.border, fill) : fill;
    const actual = contrastRatio(frame, around);
    scan.checked += 1;
    if (actual < 3) {
      scan.controlFailures.push(
        `${actual.toFixed(2)}:1 < 3:1 ${field.border ? 'border' : 'fill'} of ${field.label}`,
      );
    }
  }

  const ring = painted.tokens['--color-focus-ring'];
  if (ring) {
    for (const name of SURFACE_TOKENS) {
      const surface = painted.tokens[name];
      if (!surface) continue;
      const actual = contrastRatio(ring, surface);
      scan.checked += 1;
      if (actual < 3)
        scan.controlFailures.push(`${actual.toFixed(2)}:1 < 3:1 focus ring on ${name}`);
    }
  }
  return scan;
}
