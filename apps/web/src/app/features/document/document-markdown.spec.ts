// Tests for the generated-document parser: structure, the stored comment, bold titles and plain-text safety.
import { describe, expect, it } from 'vitest';

import { parseDocument } from './document-markdown';

describe('parseDocument', () => {
  it('drops the leading comment with the prompt version and model files', () => {
    const blocks = parseDocument(
      '<!--\npromptVersion: v1\nmodelFiles:\n  - a sha256:1\n-->\n\nHello.',
    );

    expect(blocks).toEqual([{ kind: 'paragraph', inlines: [{ text: 'Hello.', bold: false }] }]);
  });

  it('reads headings, joined paragraph lines and both list kinds', () => {
    const blocks = parseDocument(
      [
        '# Title',
        '',
        '## Part',
        'first line',
        'second line',
        '',
        '- a',
        '- b',
        '1. one',
        '2) two',
      ].join('\n'),
    );

    expect(blocks.map((block) => block.kind)).toEqual([
      'heading',
      'heading',
      'paragraph',
      'list',
      'list',
    ]);
    expect(blocks[0]).toMatchObject({ level: 3 });
    expect(blocks[2]).toMatchObject({
      inlines: [{ text: 'first line second line', bold: false }],
    });
    expect(blocks[3]).toMatchObject({ ordered: false });
    expect((blocks[3] as { items: unknown[] }).items).toHaveLength(2);
    expect(blocks[4]).toMatchObject({ ordered: true });
  });

  it('treats a line that is only bold as a section title', () => {
    const blocks = parseDocument('**1. Summary**\nThe customer opens **Search** now.');

    expect(blocks[0]).toEqual({
      kind: 'heading',
      level: 3,
      inlines: [{ text: '1. Summary', bold: false }],
    });
    expect(blocks[1]).toEqual({
      kind: 'paragraph',
      inlines: [
        { text: 'The customer opens ', bold: false },
        { text: 'Search', bold: true },
        { text: ' now.', bold: false },
      ],
    });
  });

  it('keeps citations such as [00:20-00:31] as text', () => {
    const [block] = parseDocument('Done [00:20-00:31].');

    expect(block).toEqual({
      kind: 'paragraph',
      inlines: [{ text: 'Done [00:20-00:31].', bold: false }],
    });
  });

  it('keeps markup-like text as plain text and an unmatched ** unchanged', () => {
    const [block] = parseDocument('<script>alert(1)</script> and **open');

    expect(block).toEqual({
      kind: 'paragraph',
      inlines: [{ text: '<script>alert(1)</script> and **open', bold: false }],
    });
  });

  it('returns no block for an empty document', () => {
    expect(parseDocument('')).toEqual([]);
    expect(parseDocument('<!-- only a comment -->\n')).toEqual([]);
  });
});
