// Segmented gauge primitive: real, determinate progress drawn as discrete cells.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Largest number of cells drawn; more would make the cells too thin to read. */
export const MAX_GAUGE_SEGMENTS = 40;

/**
 * Number of filled cells for `percent` of `segments` cells. Rounds down so the gauge never shows
 * more than was done, and fills every cell only at 100. A non-finite percent counts as 0.
 */
export function filledSegments(percent: number, segments: number): number {
  const cells = Math.min(MAX_GAUGE_SEGMENTS, Math.max(1, Math.floor(segments)));
  if (!Number.isFinite(percent) || percent <= 0) return 0;
  if (percent >= 100) return cells;
  return Math.min(cells - 1, Math.floor((percent / 100) * cells));
}

/**
 * Determinate gauge for stages, uploads, model downloads and the recording level. Use the activity
 * line instead when no real percentage exists.
 */
@Component({
  selector: 'ns-segmented-gauge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ns-gauge" role="img" [attr.aria-label]="label()">
      @for (filled of cells(); track $index) {
        <span class="ns-gauge__segment" [class.ns-gauge__segment--filled]="filled"></span>
      }
    </div>
  `,
})
export class SegmentedGaugeComponent {
  /** Completed share from 0 to 100. */
  readonly percent = input.required<number>();
  readonly segments = input(20);
  /** Translated text that includes the value, for example "Transcription 40%". */
  readonly label = input.required<string>();
  protected readonly cells = computed(() => {
    const total = Math.min(MAX_GAUGE_SEGMENTS, Math.max(1, Math.floor(this.segments())));
    const filled = filledSegments(this.percent(), total);
    return Array.from({ length: total }, (_, index) => index < filled);
  });
}
