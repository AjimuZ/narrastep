// English and Italian texts of the desktop shell's own dialog and fallback page (ADR 0015).
'use strict';

/**
 * Texts by language. `{url}` in `unavailableBody` is replaced with the app URL. Italian follows
 * `apps/web/src/locale/GLOSSARY.md`: "tu" form, sentence case, product name untranslated.
 */
const MESSAGES = Object.freeze({
  en: Object.freeze({
    unavailableTitle: 'Narrastep is not reachable',
    unavailableBody:
      'The window could not open {url}. Start Narrastep with ./start.sh, wait until it is ready, then try again.',
    unavailableRetry: 'Try again',
    leaveTitle: 'Leave this page?',
    leaveDetail: 'A recording, an upload or unsaved edits may be lost.',
    leaveStay: 'Stay',
    leaveConfirm: 'Leave',
  }),
  it: Object.freeze({
    unavailableTitle: 'Narrastep non è raggiungibile',
    unavailableBody:
      'La finestra non è riuscita ad aprire {url}. Avvia Narrastep con ./start.sh, attendi che sia pronto, poi riprova.',
    unavailableRetry: 'Riprova',
    leaveTitle: 'Uscire da questa pagina?',
    leaveDetail:
      'Una registrazione, un caricamento o modifiche non salvate potrebbero andare persi.',
    leaveStay: 'Resta',
    leaveConfirm: 'Esci',
  }),
});

/** Returns the texts for `language`; any value other than `'it'` gets English. */
function messagesFor(language) {
  return language === 'it' ? MESSAGES.it : MESSAGES.en;
}

module.exports = { MESSAGES, messagesFor };
