// Confirmation dialog shared by every screen for destructive actions.
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';

export interface ConfirmDialogData {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
}

/**
 * Modal confirmation for destructive or irreversible actions. Closes with `true` only when the user
 * presses the confirm button; Cancel closes with `false`.
 */
@Component({
  selector: 'ns-confirm-dialog',
  template: `
    <section class="ns-dialog" aria-labelledby="confirm-title" aria-describedby="confirm-message">
      <h2 id="confirm-title" class="ns-dialog__title">{{ data.title }}</h2>
      <div class="ns-dialog__body">
        <p id="confirm-message" class="muted">{{ data.message }}</p>
      </div>
      <div class="ns-dialog__actions">
        <button class="ns-button" type="button" (click)="close(false)">Cancel</button>
        <button
          class="ns-button"
          [class.ns-button--danger]="data.danger"
          [class.ns-button--primary]="!data.danger"
          type="button"
          (click)="close(true)"
        >
          {{ data.confirmLabel }}
        </button>
      </div>
    </section>
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
