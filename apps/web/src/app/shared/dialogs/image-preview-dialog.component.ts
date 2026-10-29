// Image preview dialog: one full-size local screenshot over the page, closed with a button or Escape.
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';

import { PixelIconComponent } from '../ui';

export interface ImagePreviewDialogData {
  imageUrl: string;
  alt: string;
  timestampLabel: string;
}

/** Full-size screenshot preview titled with its video time; closes with no value. */
@Component({
  selector: 'ns-image-preview-dialog',
  imports: [PixelIconComponent],
  template: `
    <section class="ns-dialog preview" aria-labelledby="preview-title">
      <h2 id="preview-title" class="ns-dialog__title" i18n="@@preview.title">
        Screenshot at <span class="mono">{{ data.timestampLabel }}</span>
      </h2>
      <div class="ns-dialog__body">
        <img [src]="data.imageUrl" [alt]="data.alt" />
      </div>
      <div class="ns-dialog__actions">
        <button class="ns-button" type="button" (click)="close()">
          <ns-pixel-icon name="close" /><span i18n="@@dialog.close">Close</span>
        </button>
      </div>
    </section>
  `,
  styles: `
    .preview {
      width: min(72rem, calc(100vw - 2rem));
    }

    img {
      display: block;
      width: 100%;
      max-height: calc(100dvh - 11rem);
      border: 1px solid var(--frame);
      border-radius: var(--radius);
      object-fit: contain;
      background: var(--color-media-bg);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ImagePreviewDialogComponent {
  readonly data = inject<ImagePreviewDialogData>(DIALOG_DATA);
  private readonly dialogRef = inject(DialogRef<void>);

  /** Returns keyboard focus to the control that opened the preview. */
  close(): void {
    this.dialogRef.close();
  }
}
