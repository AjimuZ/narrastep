// Glossary text helpers: one term per line in the forms, a list of terms in the API.

/** Largest number of terms the API accepts (backend `MAX_GLOSSARY_TERMS`). */
export const MAX_GLOSSARY_TERMS = 200;
/** Longest term the API accepts, in characters (backend `MAX_GLOSSARY_TERM_LENGTH`). */
export const MAX_GLOSSARY_TERM_LENGTH = 80;

/**
 * Splits the text of a glossary field into terms the way the API normalizes them: one term per
 * line, outer and repeated spaces removed, blank lines dropped, repeats ignored without regard to
 * case. Order is kept because the transcriber uses the first terms when the list is long.
 */
export function parseGlossary(text: string): string[] {
  const seen = new Set<string>();
  const terms: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const term = line.trim().replace(/\s+/g, ' ');
    const key = term.toLowerCase();
    if (term && !seen.has(key)) {
      seen.add(key);
      terms.push(term);
    }
  }
  return terms;
}

/** Joins saved terms into the text shown in a glossary field, one per line. */
export function glossaryText(terms: readonly string[]): string {
  return terms.join('\n');
}

/**
 * Reports why a glossary field cannot be sent: `'count'` for more than `MAX_GLOSSARY_TERMS` terms,
 * `'length'` for a term over `MAX_GLOSSARY_TERM_LENGTH` characters, `null` when it is valid.
 */
export function glossaryProblem(text: string): 'count' | 'length' | null {
  const terms = parseGlossary(text);
  if (terms.length > MAX_GLOSSARY_TERMS) {
    return 'count';
  }
  return terms.some((term) => term.length > MAX_GLOSSARY_TERM_LENGTH) ? 'length' : null;
}

/** Whether two term lists are equal in order and spelling (used to send only real changes). */
export function sameGlossary(first: readonly string[], second: readonly string[]): boolean {
  return first.length === second.length && first.every((term, index) => term === second[index]);
}
