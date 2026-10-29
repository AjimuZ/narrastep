import { DEFAULT_DIALOG_CONFIG, DialogConfig } from '@angular/cdk/dialog';
import { ApplicationConfig, LOCALE_ID, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { ACTIVE_LANGUAGE } from './core/i18n/init';
import { apiErrorInterceptor } from './core/services/api-error.interceptor';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    { provide: LOCALE_ID, useValue: ACTIVE_LANGUAGE },
    provideBrowserGlobalErrorListeners(),
    provideHttpClient(withInterceptors([apiErrorInterceptor])),
    provideRouter(routes, withComponentInputBinding()),
    // Every dialog is modal for assistive technology; CDK defaults `aria-modal` to false. The token
    // replaces the whole default configuration, so it starts from `new DialogConfig()` (role, focus).
    {
      provide: DEFAULT_DIALOG_CONFIG,
      useFactory: () => ({ ...new DialogConfig(), ariaModal: true }),
    },
  ],
};
