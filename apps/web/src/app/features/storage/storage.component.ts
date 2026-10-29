// Storage screen: the space each project uses and the guarded removal of an original video after export.
import { DatePipe } from '@angular/common';
import { Dialog, DialogModule } from '@angular/cdk/dialog';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, filter, finalize, switchMap } from 'rxjs';

import { ProjectStorage, RemovalBlockedReason, StorageSummary } from '../../core/api.models';
import { errorText } from '../../core/i18n/error-text';
import { formatFileSize } from '../../core/i18n/format';
import { ApiService } from '../../core/services/api.service';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../shared/dialogs/confirm-dialog.component';
import { StatusLabel, projectStatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';

/** One labelled share of a project's disk use, in the order the screen lists them. */
export interface StorageShare {
  label: string;
  bytes: number;
}

/**
 * Lists every project with the space it uses and, for a project whose current review is already
 * in a package, offers to remove the original video. The removal always goes through a
 * confirmation dialog; the API refuses it before an export, and the button is then replaced by
 * the reason.
 */
@Component({
  selector: 'ns-storage',
  imports: [
    ActivityLineComponent,
    DatePipe,
    DialogModule,
    PixelIconComponent,
    RouterLink,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './storage.component.html',
  styleUrl: './storage.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StorageComponent {
  readonly summary = signal<StorageSummary | null>(null);
  readonly loading = signal(true);
  readonly errorMessage = signal<string | null>(null);
  /** Project whose original video is being removed; its button is disabled meanwhile. */
  readonly removingProjectId = signal<string | null>(null);
  /** Result of the last removal, announced politely to screen readers. */
  readonly notice = signal<string | null>(null);

  private readonly api = inject(ApiService);
  private readonly dialog = inject(Dialog);
  private readonly destroyRef = inject(DestroyRef);

  /** Loads the figures when the route is entered. */
  constructor() {
    this.load();
  }

  /** Reloads the figures and exposes a recoverable error state. */
  load(): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.api
      .getStorage()
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error));
          return EMPTY;
        }),
      )
      .subscribe((summary) => this.summary.set(summary));
  }

  /**
   * Asks for confirmation, naming the project and the space that will be freed, then removes the
   * original video. Cancelling or pressing Escape changes nothing; an API refusal is shown above
   * the list. On success the row and the total are updated from the answer.
   */
  confirmRemoval(item: ProjectStorage): void {
    const data: ConfirmDialogData = {
      title: $localize`:@@storage.remove_title:Remove the original video of “${item.name}:NAME:”?`,
      message: $localize`:@@storage.remove_message:This frees ${this.size(item.categories.source)}:SIZE: on this computer. Packages you already created are kept. Afterwards the video cannot be played, exact frames cannot be recovered, and a new package can only use screenshots that are already saved as images. This cannot be undone.`,
      confirmLabel: $localize`:@@storage.remove_confirm:Remove original video`,
      danger: true,
    };

    this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: $localize`:@@storage.remove_aria:Confirm removal of the original video of ${item.name}:NAME:`,
        disableClose: true,
      })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        switchMap(() => {
          this.removingProjectId.set(item.projectId);
          this.errorMessage.set(null);
          this.notice.set(null);
          return this.api.removeSourceVideo(item.projectId).pipe(
            finalize(() => this.removingProjectId.set(null)),
            catchError((error: unknown) => {
              this.errorMessage.set(this.errorText(error));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((updated) => {
        const freed = item.totalBytes - updated.totalBytes;
        this.summary.update((current) =>
          current === null
            ? current
            : {
                ...current,
                totalBytes: Math.max(0, current.totalBytes - freed),
                items: current.items.map((entry) =>
                  entry.projectId === updated.projectId ? updated : entry,
                ),
              },
        );
        this.notice.set(
          $localize`:@@storage.removed_notice:Original video of ${item.name}:NAME: removed. ${this.size(Math.max(0, freed))}:SIZE: freed.`,
        );
      });
  }

  /** Formats a byte count with the active language's separators. */
  size(bytes: number): string {
    return formatFileSize(bytes);
  }

  /** State word and pill tone of the project. */
  statusLabel(item: ProjectStorage): StatusLabel {
    return projectStatusLabel(item.status);
  }

  /** The parts of a project's use, without the empty ones, in a fixed order. */
  shares(item: ProjectStorage): StorageShare[] {
    const parts = item.categories;
    return [
      { label: $localize`:@@storage.cat.source:Original video`, bytes: parts.source },
      { label: $localize`:@@storage.cat.audio:Audio`, bytes: parts.audio },
      { label: $localize`:@@storage.cat.transcript:Transcript`, bytes: parts.transcript },
      { label: $localize`:@@storage.cat.screenshots:Screenshots`, bytes: parts.screenshots },
      { label: $localize`:@@storage.cat.exports:Packages`, bytes: parts.exports },
      { label: $localize`:@@storage.cat.other:Other`, bytes: parts.other },
    ].filter((share) => share.bytes > 0);
  }

  /** Why the original video cannot be removed now, in the active language. */
  blockedText(reason: RemovalBlockedReason): string {
    switch (reason) {
      case 'no_video':
        return $localize`:@@storage.blocked.no_video:This project has no original video yet.`;
      case 'already_removed':
        return $localize`:@@storage.blocked.already_removed:The original video was removed.`;
      case 'busy':
        return $localize`:@@storage.blocked.busy:Wait until the project finishes its current work.`;
      case 'package_required':
        return $localize`:@@storage.blocked.package_required:Create a package of the current review first.`;
    }
  }

  /** Whether the row can send the user to the export screen to create the missing package. */
  canCreatePackage(item: ProjectStorage): boolean {
    return (
      item.removalBlockedReason === 'package_required' &&
      (item.status === 'review' || item.status === 'completed')
    );
  }

  /** Accessible name of the Remove button, which shows only a verb. */
  removeLabel(item: ProjectStorage): string {
    return $localize`:@@storage.remove_aria_label:Remove the original video of ${item.name}:NAME:`;
  }

  /** Accessible name of the link to the export screen. */
  packageLabel(item: ProjectStorage): string {
    return $localize`:@@storage.package_aria_label:Create a package for ${item.name}:NAME:`;
  }

  /** Title of the list window with the number of projects. */
  listTitle(count: number): string {
    return $localize`:@@storage.list_title:Projects (${count}:COUNT:)`;
  }

  /** Translates a known API error code, else the API message; hides unknown implementation details. */
  private errorText(error: unknown): string {
    return errorText(
      error,
      $localize`:@@storage.error_unreachable:Narrastep could not reach its local service. Check that it is running, then try again.`,
    );
  }
}
