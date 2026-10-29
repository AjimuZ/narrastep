// Unit tests for the split dialog: validation of the split time and the returned request.
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import {
  SplitSegmentDialogComponent,
  SplitSegmentDialogData,
} from './split-segment-dialog.component';

describe('SplitSegmentDialogComponent', () => {
  /** Renders the dialog with a 1–5 s segment split at 3 s and a spy dialog reference. */
  async function setup(): Promise<{
    root: HTMLElement;
    component: SplitSegmentDialogComponent;
    detect: () => void;
    close: ReturnType<typeof vi.fn>;
  }> {
    const data: SplitSegmentDialogData = {
      leftReviewedText: 'Select ',
      rightReviewedText: 'Search.',
      splitSeconds: 3,
      minSeconds: 1,
      maxSeconds: 5,
    };
    const close = vi.fn();
    await TestBed.configureTestingModule({
      imports: [SplitSegmentDialogComponent],
      providers: [
        { provide: DIALOG_DATA, useValue: data },
        { provide: DialogRef, useValue: { close } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(SplitSegmentDialogComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return {
      root: fixture.nativeElement as HTMLElement,
      component: fixture.componentInstance,
      detect: () => fixture.detectChanges(),
      close,
    };
  }

  /** Finds a dialog button by its visible text. */
  function button(root: HTMLElement, text: string): HTMLButtonElement {
    return [...root.querySelectorAll('button')].find(
      (item) => item.textContent?.trim() === text,
    ) as HTMLButtonElement;
  }

  it('returns both text parts and the split time when confirmed', async () => {
    const { root, close } = await setup();

    expect(root.querySelector('.ns-dialog__title')?.textContent).toContain(
      'Split transcript segment',
    );
    button(root, 'Split segment').click();

    expect(close).toHaveBeenCalledWith({
      leftReviewedText: 'Select ',
      rightReviewedText: 'Search.',
      splitSeconds: 3,
    });
  });

  it('marks a time outside the segment as invalid and blocks the split', async () => {
    const { root, component, detect, close } = await setup();

    component.setSplitSeconds(5);
    detect();

    const input = root.querySelector('#split-seconds') as HTMLInputElement;
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('outside the segment');
    expect(button(root, 'Split segment').disabled).toBe(true);
    component.confirm();
    expect(close).not.toHaveBeenCalled();
  });
});
