// Behavior tests of the shared pixel primitives and the integrity of the icon sprite and font files.
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
  ActivityLineComponent,
  PIXEL_ICON_NAMES,
  PixelIconComponent,
  SegmentedGaugeComponent,
  StatusPillComponent,
  TabsComponent,
  WindowComponent,
  filledSegments,
} from './index';

// Vitest runs from apps/web, the folder that owns `icons/` and `public/`.
const iconsDir = 'icons';
const spritePath = 'public/icons/sprite.svg';

@Component({
  imports: [
    ActivityLineComponent,
    PixelIconComponent,
    SegmentedGaugeComponent,
    StatusPillComponent,
    TabsComponent,
    WindowComponent,
  ],
  template: `
    <ns-window title="Export" [primary]="true"><p>Body</p></ns-window>
    <ns-pixel-icon name="play" />
    <ns-status-pill label="Ready" tone="ok" />
    <ns-segmented-gauge [percent]="percent()" [segments]="10" label="Upload 40%" />
    <ns-activity-line label="Reading the video" />
    <ns-tabs
      label="Sections"
      idPrefix="s"
      [tabs]="[
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
        { id: 'c', label: 'C' },
      ]"
      [(selected)]="selected"
    />
  `,
})
class HostComponent {
  readonly percent = signal(40);
  readonly selected = signal('a');
}

function render() {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.detectChanges();
  return fixture;
}

describe('shared pixel primitives', () => {
  it('names the window by its title and marks only primary windows', () => {
    const element: HTMLElement = render().nativeElement;
    const section = element.querySelector('section.ns-window') as HTMLElement;
    const title = section.querySelector('h2') as HTMLElement;
    expect(section.getAttribute('aria-labelledby')).toBe(title.id);
    expect(title.textContent).toBe('Export');
    expect(section.classList).toContain('ns-window--primary');
    expect(section.textContent).toContain('Body');
  });

  it('draws an icon from the sprite and hides it from assistive technology', () => {
    const svg = (render().nativeElement as HTMLElement).querySelector('svg.ns-icon') as SVGElement;
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect(svg.querySelector('use')?.getAttribute('href')).toBe('icons/sprite.svg#i-play');
  });

  it('shows the status as a word with its tone', () => {
    const pill = (render().nativeElement as HTMLElement).querySelector('.ns-pill') as HTMLElement;
    expect(pill.textContent).toBe('Ready');
    expect(pill.classList).toContain('ns-pill--ok');
  });

  it('fills the gauge from the real percentage and labels it', () => {
    const fixture = render();
    const read = () => {
      const gauge = (fixture.nativeElement as HTMLElement).querySelector(
        '.ns-gauge',
      ) as HTMLElement;
      return {
        label: gauge.getAttribute('aria-label'),
        total: gauge.querySelectorAll('.ns-gauge__segment').length,
        filled: gauge.querySelectorAll('.ns-gauge__segment--filled').length,
      };
    };
    expect(read()).toEqual({ label: 'Upload 40%', total: 10, filled: 4 });
    fixture.componentInstance.percent.set(100);
    fixture.detectChanges();
    expect(read().filled).toBe(10);
  });

  it('rounds the gauge down and handles out-of-range values', () => {
    expect(filledSegments(99, 10)).toBe(9);
    expect(filledSegments(0.5, 10)).toBe(0);
    expect(filledSegments(-5, 10)).toBe(0);
    expect(filledSegments(Number.NaN, 10)).toBe(0);
    expect(filledSegments(250, 10)).toBe(10);
    expect(filledSegments(50, 1000)).toBe(20);
  });

  it('exposes the activity line without a value', () => {
    const line = (render().nativeElement as HTMLElement).querySelector(
      '.ns-activity',
    ) as HTMLElement;
    expect(line.getAttribute('role')).toBe('progressbar');
    expect(line.getAttribute('aria-label')).toBe('Reading the video');
    expect(line.hasAttribute('aria-valuenow')).toBe(false);
  });

  it('selects tabs with the keyboard and keeps one tab in the tab order', () => {
    const fixture = render();
    const element: HTMLElement = fixture.nativeElement;
    const tabs = () => Array.from(element.querySelectorAll<HTMLElement>('[role="tab"]'));
    const press = (index: number, key: string) =>
      tabs()[index]?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    expect(tabs().map((tab) => tab.getAttribute('aria-selected'))).toEqual([
      'true',
      'false',
      'false',
    ]);
    expect(tabs().map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
    expect(tabs()[1]?.getAttribute('aria-controls')).toBe('s-panel-b');

    press(0, 'ArrowRight');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('b');
    press(1, 'End');
    press(2, 'ArrowRight');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('a');
    tabs()[0]?.click();
    press(0, 'ArrowLeft');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('c');
  });
});

describe('pixel icon sprite', () => {
  const grids = readdirSync(iconsDir).filter((file) => file.endsWith('.txt'));
  const sprite = readFileSync(spritePath, 'utf8');

  it('has a 16x16 grid for every icon name and no unlisted grid', () => {
    expect(grids.map((file) => file.replace('.txt', '')).sort()).toEqual(
      [...PIXEL_ICON_NAMES].sort(),
    );
    for (const file of grids) {
      const rows = readFileSync(`${iconsDir}/${file}`, 'utf8').trimEnd().split('\n');
      expect(rows.length, file).toBe(16);
      for (const row of rows) expect(row, file).toMatch(/^[#.]{16}$/);
    }
  });

  it('is generated from the grids: one 16x16 symbol per name', () => {
    for (const name of PIXEL_ICON_NAMES) {
      expect(sprite, name).toContain(`<symbol id="i-${name}" viewBox="0 0 16 16">`);
    }
    expect(sprite.match(/<symbol /g)?.length).toBe(PIXEL_ICON_NAMES.length);
  });
});

describe('bundled Pixelify Sans', () => {
  it('matches the hash recorded in THIRD_PARTY_NOTICES.md', () => {
    const hash = createHash('sha256')
      .update(readFileSync('public/fonts/PixelifySans.ttf'))
      .digest('hex');
    const notices = readFileSync('../../THIRD_PARTY_NOTICES.md', 'utf8');
    expect(notices).toContain(hash);
  });

  it('ships its license text next to the font', () => {
    expect(readFileSync('public/fonts/OFL.txt', 'utf8')).toContain(
      'SIL OPEN FONT LICENSE Version 1.1',
    );
  });
});
