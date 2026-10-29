import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';

export interface ConfirmDialogData {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
}

@Component({
  selector: 'ns-confirm-dialog',
  template: `
    <section class="dialog" aria-labelledby="confirm-title" aria-describedby="confirm-message">
      <p class="eyebrow">Confirmation required</p>
      <h2 id="confirm-title">{{ data.title }}</h2>
      <p id="confirm-message" class="muted">{{ data.message }}</p>
      <div class="dialog__actions">
        <button class="button button--quiet" type="button" (click)="close(false)">Cancel</button>
        <button
          class="button"
          [class.button--danger]="data.danger"
          [class.button--primary]="!data.danger"
          type="button"
          (click)="close(true)"
        >
          {{ data.confirmLabel }}
        </button>
      </div>
    </section>
  `,
  styles: `
    .dialog {
      width: min(30rem, calc(100vw - 2rem));
      max-height: calc(100dvh - 2rem);
      overflow: auto;
      padding: var(--space-5);
      border: 1px solid var(--color-border-accent);
      border-radius: var(--radius);
      background: var(--color-bg-elevated);
      box-shadow: 0 24px 80px var(--color-shadow-strong);
    }

    .dialog__actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--space-3);
      margin-top: var(--space-5);
    }

    @media (max-width: 34rem) {
      .dialog {
        width: min(100%, calc(100vw - 1rem));
        padding: var(--space-4);
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
export class ConfirmDialogComponent {
  readonly data = inject<ConfirmDialogData>(DIALOG_DATA);
  private readonly dialogRef = inject(DialogRef<boolean>);

  /** Closes the modal with an explicit user decision. */
  close(confirmed: boolean): void {
    this.dialogRef.close(confirmed);
  }
}
