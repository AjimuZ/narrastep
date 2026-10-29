// Unit tests of the glossary text helpers shared by the New project form and the Process dialog.
import { describe, expect, it } from 'vitest';

import {
  MAX_GLOSSARY_TERMS,
  MAX_GLOSSARY_TERM_LENGTH,
  glossaryProblem,
  glossaryText,
  parseGlossary,
  sameGlossary,
} from './glossary';

describe('glossary helpers', () => {
  it('parses one term per line, trimmed, single-spaced, without blanks or repeats, in order', () => {
    expect(parseGlossary('')).toEqual([]);
    expect(parseGlossary(' \n\r\n  ')).toEqual([]);
    expect(parseGlossary('  Ada   Lovelace \r\n\nada lovelace\nCittà della Pieve')).toEqual([
      'Ada Lovelace',
      'Città della Pieve',
    ]);
  });

  it('round-trips saved terms through the field text', () => {
    const terms = ['Narrastep', 'caffè'];
    expect(parseGlossary(glossaryText(terms))).toEqual(terms);
    expect(glossaryText([])).toBe('');
  });

  it('reports too many terms and too long terms at exactly the API limits', () => {
    const many = (count: number) =>
      Array.from({ length: count }, (_, index) => `term ${index}`).join('\n');
    expect(glossaryProblem('')).toBeNull();
    expect(glossaryProblem(many(MAX_GLOSSARY_TERMS))).toBeNull();
    expect(glossaryProblem(many(MAX_GLOSSARY_TERMS + 1))).toBe('count');
    expect(glossaryProblem('x'.repeat(MAX_GLOSSARY_TERM_LENGTH))).toBeNull();
    expect(glossaryProblem('x'.repeat(MAX_GLOSSARY_TERM_LENGTH + 1))).toBe('length');
  });

  it('compares term lists by order and spelling', () => {
    expect(sameGlossary(['a', 'b'], ['a', 'b'])).toBe(true);
    expect(sameGlossary(['a', 'b'], ['b', 'a'])).toBe(false);
    expect(sameGlossary(['a'], [])).toBe(false);
  });
});
