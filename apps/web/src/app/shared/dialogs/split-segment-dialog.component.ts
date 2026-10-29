import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { FormsModule } from '@angular/forms';

import { SplitSegmentRequest } from '../../core/api.models';

export interface SplitSegmentDialogData extends SplitSegmentRequest {
  minSeconds: number;
  maxSeconds: number;
}

@Component({
  selector: 'ns-split-segment-dialog',
  imports: [FormsModule],
  template: `
    <section class="dialog" aria-labelledby="split-title" aria-describedby="split-description">
      <p class="eyebrow">Structural edit</p>
      <h2 id="split-title">Split transcript segment</h2>
      <p id="split-description" class="muted">
        Confirm the exact text portions and choose where the new timestamp boundary belongs.
      </p>

      <div class="split-copy">
        <article>
          <h3>First segment</h3>
          <p>{{ data.leftReviewedText }}</p>
        </article>
        <article>
          <h3>Second segment</h3>
          <p>{{ data.rightReviewedText }}</p>
        </article>
      </div>

      <div class="field">
        <label for="split-seconds">Split timestamp in seconds</label>
        <input
          id="split-seconds"
          type="number"
          step="0.001"
          [min]="data.minSeconds"
          [max]="data.maxSeconds"
          [ngModel]="splitSeconds()"
          (ngModelChange)="setSplitSeconds($event)"
          aria-describedby="split-range"
        />
        <small id="split-range" class="muted">
          Allowed range: {{ data.minSeconds.toFixed(3) }}–{{ data.maxSeconds.toFixed(3) }}
          seconds
        </small>
        @if (!isValid()) {
          <p class="field-error" role="alert">Choose a timestamp strictly inside the segment.</p>
        }
      </div>

      <div class="dialog__actions">
        <button class="button button--quiet" type="button" (click)="cancel()">Cancel</button>
        <button
          class="button button--primary"
          type="button"
          [disabled]="!isValid()"
          (click)="confirm()"
        >
          Split segment
        </button>
      </div>
    </section>
  `,
  styles: `
    .dialog {
      width: min(44rem, calc(100vw - 2rem));
      max-height: calc(100dvh - 2rem);
      overflow: auto;
      padding: var(--space-5);
      border: 1px solid var(--color-border-accent);
      border-radius: var(--radius);
      background: var(--color-bg-elevated);
      box-shadow: 0 24px 80px var(--color-shadow-strong);
    }

    .split-copy {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: var(--space-3);
      margin-block: var(--space-5);
    }

    article {
      min-height: 8rem;
      padding: var(--space-4);
      border: 1px solid var(--color-border);
      border-radius: var(--radius);
      background: var(--color-bg-deep);
      white-space: pre-wrap;
    }

    article h3 {
      font-size: 0.8rem;
      text-transform: uppercase;
      color: var(--color-text-muted);
    }

    .dialog__actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--space-3);
      margin-top: var(--space-5);
    }

    @media (max-width: 38rem) {
      .dialog {
        width: min(100%, calc(100vw - 1rem));
        padding: var(--space-4);
      }

      .split-copy {
        grid-template-columns: 1fr;
      }

      .dialog__actions {
        align-items: stretch;
        flex-direction: column-reverse;
      }

      .dialog__actions .button {
        width: 100%;
      }
    }
  `,
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
