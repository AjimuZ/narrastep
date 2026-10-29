// Split dialog: confirms the two text halves of a transcript segment and the split time.
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { FormsModule } from '@angular/forms';

import { SplitSegmentRequest } from '../../core/api.models';

export interface SplitSegmentDialogData extends SplitSegmentRequest {
  minSeconds: number;
  maxSeconds: number;
}

/**
 * Modal split confirmation. Closes with a `SplitSegmentRequest` when the time lies strictly inside
 * the segment and the user confirms; Cancel closes with no value.
 */
@Component({
  selector: 'ns-split-segment-dialog',
  imports: [FormsModule],
  template: `
    <section
      class="ns-dialog split-dialog"
      aria-labelledby="split-title"
      aria-describedby="split-description"
    >
      <h2 id="split-title" class="ns-dialog__title" i18n="@@split.title">
        Split transcript segment
      </h2>
      <div class="ns-dialog__body">
        <p id="split-description" class="muted" i18n="@@split.description">
          Check the two parts and the split time.
        </p>

        <div class="split-copy">
          <article>
            <h3 i18n="@@split.first">First segment</h3>
            <p>{{ data.leftReviewedText }}</p>
          </article>
          <article>
            <h3 i18n="@@split.second">Second segment</h3>
            <p>{{ data.rightReviewedText }}</p>
          </article>
        </div>

        <div class="ns-form-row">
          <label class="ns-label" for="split-seconds" i18n="@@split.time_label">
            Split time (seconds)
          </label>
          <input
            id="split-seconds"
            class="ns-field mono"
            type="number"
            step="0.001"
            [min]="data.minSeconds"
            [max]="data.maxSeconds"
            [ngModel]="splitSeconds()"
            (ngModelChange)="setSplitSeconds($event)"
            [attr.aria-invalid]="isValid() ? null : 'true'"
            aria-describedby="split-range"
          />
          <p id="split-range" class="ns-help" i18n="@@split.range">
            Between {{ data.minSeconds.toFixed(3) }} and {{ data.maxSeconds.toFixed(3) }} seconds.
          </p>
          @if (!isValid()) {
            <p class="ns-error" role="alert" i18n="@@split.invalid">
              The time is outside the segment. Enter a time between its start and end.
            </p>
          }
        </div>
      </div>

      <div class="ns-dialog__actions">
        <button class="ns-button" type="button" (click)="cancel()" i18n="@@dialog.cancel">
          Cancel
        </button>
        <button
          class="ns-button ns-button--primary"
          type="button"
          [disabled]="!isValid()"
          (click)="confirm()"
          i18n="@@split.confirm"
        >
          Split segment
        </button>
      </div>
    </section>
  `,
  styles: `
    .split-dialog {
      width: min(44rem, calc(100vw - 2rem));
    }

    .split-dialog .muted {
      margin: 0;
    }

    .split-copy {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
      gap: var(--space-3);
      margin-block: var(--space-4);
    }

    article {
      min-width: 0;
      min-height: 6rem;
      padding: var(--space-3);
      border: 1px solid var(--frame);
      border-radius: var(--radius);
      background: var(--color-bg-deep);
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }

    article h3 {
      margin: 0 0 var(--space-2);
      color: var(--color-text-muted);
      font-size: var(--text-sm);
    }

    article p {
      margin: 0;
    }
  `,
  host: { '(keydown.escape)': 'cancel()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SplitSegmentDialogComponent {
  readonly data = inject<SplitSegmentDialogData>(DIALOG_DATA);
  readonly splitSeconds = signal(this.data.splitSeconds);
  readonly isValid = computed(
    () =>
      Number.isFinite(this.splitSeconds()) &&
      this.splitSeconds() > this.data.minSeconds &&
      this.splitSeconds() < this.data.maxSeconds,
  );
  private readonly dialogRef = inject(DialogRef<SplitSegmentRequest>);

  /** Converts the number input into a finite split timestamp. */
  setSplitSeconds(value: string | number): void {
    this.splitSeconds.set(Number(value));
  }

  /** Closes without mutating the transcript. */
  cancel(): void {
    this.dialogRef.close();
  }

  /** Returns the reviewed text and validated boundary to the review workspace. */
  confirm(): void {
    if (!this.isValid()) {
      return;
    }
    this.dialogRef.close({
      leftReviewedText: this.data.leftReviewedText,
      rightReviewedText: this.data.rightReviewedText,
      splitSeconds: this.splitSeconds(),
    });
  }
}
