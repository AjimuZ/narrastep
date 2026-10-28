import { Dialog, DialogModule } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import {
  ImagePreviewDialogComponent,
  ImagePreviewDialogData,
} from './image-preview-dialog.component';

describe('ImagePreviewDialogComponent', () => {
  it('closes with Escape and restores focus to the keyboard trigger', async () => {
    await TestBed.configureTestingModule({ imports: [DialogModule] }).compileComponents();
    const trigger = document.createElement('button');
    trigger.textContent = 'Preview screenshot';
    document.body.appendChild(trigger);
    trigger.focus();
    const dialog = TestBed.inject(Dialog);
    const data: ImagePreviewDialogData = {
      imageUrl: '/api/v1/screenshots/shot-1/content',
      alt: 'Screenshot at 00:00:03.000',
      timestampLabel: '00:00:03.000',
    };
    const reference = dialog.open(ImagePreviewDialogComponent, {
      data,
      ariaLabel: 'Full-size screenshot preview',
    });
    const closed = vi.fn();
    reference.closed.subscribe(closed);
    await Promise.resolve();

    const escapeEvent = new KeyboardEvent('keydown', {
      key: 'Escape',
      code: 'Escape',
      bubbles: true,
    });
    Object.defineProperty(escapeEvent, 'keyCode', { value: 27 });
    document.body.dispatchEvent(escapeEvent);
    await Promise.resolve();

    expect(closed).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});
