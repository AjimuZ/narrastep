import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';

export interface ImagePreviewDialogData {
  imageUrl: string;
  alt: string;
  timestampLabel: string;
}

@Component({
  selector: 'ns-image-preview-dialog',
  template: `
    <section class="preview" aria-labelledby="preview-title">
      <header>
        <div>
          <p class="eyebrow">Screenshot preview</p>
          <h2 id="preview-title">{{ data.timestampLabel }}</h2>
        </div>
        <button class="button button--quiet button--small" type="button" (click)="close()">
          Close
        </button>
      </header>
      <img [src]="data.imageUrl" [alt]="data.alt" />
    </section>
  `,
  styles: `
    .preview {
      width: min(72rem, calc(100vw - 2rem));
      max-height: calc(100dvh - 2rem);
      overflow: auto;
      padding: var(--space-4);
      border: 1px solid var(--color-border-accent);
      border-radius: var(--radius);
      background: var(--color-bg-elevated);
      box-shadow: 0 24px 80px var(--color-shadow-strong);
    }

    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-4);
    }

    img {
      display: block;
      width: 100%;
      max-height: calc(100vh - 9rem);
      border-radius: var(--radius);
      object-fit: contain;
      background: var(--color-media-bg);
    }

    @media (max-width: 34rem) {
      .preview {
        width: min(100%, calc(100vw - 1rem));
        padding: var(--space-3);
      }

      header {
        align-items: stretch;
        flex-direction: column;
      }

      header .button {
        width: 100%;
      }

      img {
        max-height: calc(100dvh - 13rem);
      }
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
