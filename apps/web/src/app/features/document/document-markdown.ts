// Turns the generated Markdown into a few typed blocks so the Document screen renders it without innerHTML.

/** A run of text inside a block; `bold` comes from `**…**`. */
export interface DocumentInline {
  text: string;
  bold: boolean;
}

/** One block of the document: a heading, a paragraph, or a bulleted or numbered list. */
export type DocumentBlock =
  | { kind: 'heading'; level: 3 | 4; inlines: DocumentInline[] }
  | { kind: 'paragraph'; inlines: DocumentInline[] }
  | { kind: 'list'; ordered: boolean; items: DocumentInline[][] };

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const BOLD_LINE = /^\*\*([^*]+)\*\*:?$/;

/**
 * Parses the stored `document.md`. The leading comment that records the prompt version and model
 * files is dropped. A line that is only `**bold**` is a heading, because the small local model
 * often writes section titles that way. Anything not understood stays a paragraph, so no text is
 * lost, and the result never contains markup: the template prints text only.
 */
export function parseDocument(markdown: string): DocumentBlock[] {
  const body = markdown.replace(/^\s*<!--[\s\S]*?-->/, '');
  const blocks: DocumentBlock[] = [];
  let paragraph: string[] = [];

  const flushParagraph = (): void => {
    if (paragraph.length > 0) {
      blocks.push({ kind: 'paragraph', inlines: inlines(paragraph.join(' ')) });
      paragraph = [];
    }
  };

  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    const heading = HEADING.exec(line);
    const boldLine = BOLD_LINE.exec(line.trim());
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);

    if (line.trim() === '') {
      flushParagraph();
    } else if (heading) {
      flushParagraph();
      // The page owns h1 and the result window h2, so the document starts at h3.
      const level = heading[1]!.length <= 2 ? 3 : 4;
      blocks.push({ kind: 'heading', level, inlines: inlines(heading[2]!) });
    } else if (boldLine) {
      flushParagraph();
      blocks.push({ kind: 'heading', level: 3, inlines: [{ text: boldLine[1]!, bold: false }] });
    } else if (bullet || numbered) {
      flushParagraph();
      const ordered = !bullet;
      const text = (bullet ?? numbered)![1]!;
      const last = blocks.at(-1);
      if (last?.kind === 'list' && last.ordered === ordered) {
        last.items.push(inlines(text));
      } else {
        blocks.push({ kind: 'list', ordered, items: [inlines(text)] });
      }
    } else {
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  return blocks;
}

/** Splits `a **b** c` into plain and bold runs; an unmatched `**` stays as text. */
function inlines(text: string): DocumentInline[] {
  const runs: DocumentInline[] = [];
  const parts = text.split(/\*\*([^*]+)\*\*/);
  parts.forEach((part, index) => {
    if (part !== '') {
      runs.push({ text: part, bold: index % 2 === 1 });
    }
  });
  return runs;
}
