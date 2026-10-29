// Process dialog: asks for the project type (and optionally the spoken language) before processing starts.
import { Dialog, DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { Project, ProjectMode, StartProcessingRequest } from '../../core/api.models';
import { languageSuggestions } from '../language-options';
import { projectTypes } from '../project-types';

export interface ProcessDialogData {
  /** Project being processed; its name is shown and its type and language are preselected. */
  project: Pick<Project, 'name' | 'mode' | 'sourceLanguage'>;
}

/** Language code accepted by the form: `auto` or a two- or three-letter code (the API checks the exact list). */
const LANGUAGE_PATTERN = /^(auto|[a-z]{2,3})$/i;

/**
 * Modal that collects what the API needs to start processing: a required project type (ADR 0009,
 * P05.4) and, under "More options", the spoken language. Closes with the choices only when the user
 * presses Start processing with a type selected; Cancel and Escape close with `undefined`. The
 * caller sets `disableClose` so a click on the backdrop never decides, which also stops CDK from
 * handling Escape itself.
 */
@Component({
  selector: 'ns-process-dialog',
  templateUrl: './process-dialog.component.html',
  styleUrl: './process-dialog.component.scss',
  host: { '(keydown.escape)': 'cancel()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProcessDialogComponent {
  readonly data = inject<ProcessDialogData>(DIALOG_DATA);
  readonly projectTypes = projectTypes();
  readonly languageSuggestions = languageSuggestions();
  readonly mode = signal<ProjectMode | null>(this.data.project.mode);
  readonly language = signal(this.data.project.sourceLanguage);
  /** Set when Start was pressed without a type or with an invalid language code. */
  readonly showErrors = signal(false);
  /** Whether the "More options" disclosure is expanded; opened when it holds an error. */
  readonly moreOptionsOpen = signal(false);

  private readonly dialogRef = inject(DialogRef<StartProcessingRequest | undefined>);

  /** Title of the dialog window. */
  title(): string {
    return $localize`:@@process_dialog.title:Process “${this.data.project.name}:NAME:”`;
  }

  /** Whether the entered language is `auto` or a plausible language code. */
  languageValid(): boolean {
    return LANGUAGE_PATTERN.test(this.language().trim());
  }

  /** Records the card the user picked. */
  selectMode(mode: ProjectMode): void {
    this.mode.set(mode);
  }

  /** Records the typed language code. */
  setLanguage(event: Event): void {
    this.language.set((event.target as HTMLInputElement).value);
  }

  /** Keeps the disclosure state in sync with the native `<details>` element. */
  onMoreOptionsToggle(event: Event): void {
    this.moreOptionsOpen.set((event.target as HTMLDetailsElement).open);
  }

  /**
   * Closes with the choices when a type is selected and the language is valid; otherwise shows
   * what is missing and keeps the dialog open. The language is sent only when it changed.
   */
  start(): void {
    const mode = this.mode();
    if (mode === null || !this.languageValid()) {
      this.showErrors.set(true);
      if (!this.languageValid()) {
        this.moreOptionsOpen.set(true);
      }
      return;
    }
    const language = this.language().trim().toLowerCase();
    const choices: StartProcessingRequest = { mode };
    if (language !== this.data.project.sourceLanguage) {
      choices.sourceLanguage = language;
    }
    this.dialogRef.close(choices);
  }

  /** Closes without starting anything. */
  cancel(): void {
    this.dialogRef.close(undefined);
  }
}

/**
 * Opens the Process dialog for `project` and emits once: the choices to send with the start
 * request, or `undefined` when the user cancelled.
 */
export function openProcessDialog(
  dialog: Dialog,
  project: ProcessDialogData['project'],
): Observable<StartProcessingRequest | undefined> {
  return dialog.open<StartProcessingRequest | undefined, ProcessDialogData>(
    ProcessDialogComponent,
    {
      data: { project },
      ariaLabel: $localize`:@@process_dialog.aria:Choose the project type and start processing`,
      disableClose: true,
    },
  ).closed;
}
