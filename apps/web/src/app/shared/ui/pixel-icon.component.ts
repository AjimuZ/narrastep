// Pixel icon primitive: draws one symbol of the 16x16 sprite served at icons/sprite.svg.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Names of the symbols in `apps/web/icons/*.txt`; keep in step with the sprite. */
export const PIXEL_ICON_NAMES = [
  'check',
  'chevron-right',
  'close',
  'copy',
  'download',
  'edit',
  'export',
  'folder',
  'info',
  'mic',
  'pause',
  'play',
  'plus',
  'record',
  'refresh',
  'search',
  'settings',
  'stop',
  'trash',
  'upload',
  'warning',
] as const;

export type PixelIconName = (typeof PIXEL_ICON_NAMES)[number];

/**
 * Decorative icon (`aria-hidden`): the text or the accessible name of the surrounding control
 * carries the meaning. It takes the current text color and a fixed 16 px size.
 */
@Component({
  selector: 'ns-pixel-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg class="ns-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <use [attr.href]="href()" />
    </svg>
  `,
  styles: `
    :host {
      display: inline-flex;
    }
  `,
})
export class PixelIconComponent {
  /** Symbol to draw; a name outside `PIXEL_ICON_NAMES` fails to compile. */
  readonly name = input.required<PixelIconName>();
  protected readonly href = computed(() => `icons/sprite.svg#i-${this.name()}`);
}
