// A small BibTeX/BibLaTeX stream language for CodeMirror 6.
import { StreamLanguage } from '@codemirror/language';

// state.depth: brace nesting inside an entry (0 = between entries)
// state.expect: 'key' right after `@type{`, 'field' when a field name may come, 'value' after `=`
const bibtexMode = {
  name: 'bibtex',
  startState: () => ({ depth: 0, expect: null, inString: false }),
  copyState: (s) => ({ ...s }),
  token(stream, state) {
    if (state.inString) {
      while (!stream.eol()) {
        const ch = stream.next();
        if (ch === '\\') stream.next();
        else if (ch === '"') {
          state.inString = false;
          state.expect = 'field';
          break;
        }
      }
      return 'string';
    }

    if (stream.eatSpace()) return null;

    if (state.depth === 0) {
      if (stream.match(/^@[a-zA-Z]+/)) {
        state.expect = 'key';
        return 'keyword';
      }
      if (stream.eat('{') || stream.eat('(')) {
        state.depth = 1;
        return 'bracket';
      }
      // Anything outside entries is a comment in BibTeX.
      stream.skipToEnd();
      return 'comment';
    }

    if (state.depth === 1) {
      if (state.expect === 'key' && stream.match(/^[^,\s{}()]+/)) {
        state.expect = 'field';
        return 'labelName';
      }
      if (stream.eat(',')) {
        state.expect = 'field';
        return 'punctuation';
      }
      if (stream.eat('=')) {
        state.expect = 'value';
        return 'operator';
      }
      if (stream.eat('"')) {
        state.inString = true;
        return 'string';
      }
      if (stream.eat('#')) return 'operator';
      if (stream.eat('}') || stream.eat(')')) {
        state.depth = 0;
        state.expect = null;
        return 'bracket';
      }
      if (stream.eat('{')) {
        state.depth = 2;
        return 'bracket';
      }
      if (state.expect === 'field' && stream.match(/^[a-zA-Z_][\w\-:.]*/)) return 'propertyName';
      if (stream.match(/^\d+/)) return 'number';
      if (stream.match(/^[a-zA-Z_][\w\-:.]*/)) return 'variableName';
      stream.next();
      return null;
    }

    // Inside a braced value (depth >= 2).
    if (stream.match(/^\\[a-zA-Z]+/) || stream.match(/^\\./)) return 'keyword';
    const ch = stream.next();
    if (ch === '{') state.depth++;
    else if (ch === '}') {
      state.depth--;
      if (state.depth === 1) state.expect = 'field';
      return 'bracket';
    }
    return 'string';
  },
  languageData: {
    commentTokens: { line: '%' },
    closeBrackets: { brackets: ['{', '(', '"'] },
  },
};

export const bibtexLanguage = StreamLanguage.define(bibtexMode);
