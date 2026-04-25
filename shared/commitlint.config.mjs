// Shared commitlint config for jr200-labs repos.
//
// Extends @commitlint/config-conventional plus a `banned-words` rule that
// fails when commit messages contain any word listed in the
// BANNED_COMMIT_WORDS env var (CSV). The CI workflow plumbs that var from
// the org-level Actions variable of the same name.
//
// Consumers cache this file via shared/MANIFEST.json (node section) and
// re-export it from a top-level commitlint.config.mjs:
//
//   export { default } from './.shared/commitlint.config.mjs';

const rawList = process.env.BANNED_COMMIT_WORDS || '';
const bannedWords = rawList
  .split(',')
  .map((w) => w.trim().toLowerCase())
  .filter(Boolean);

const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const bannedWordsRule = (parsed) => {
  if (bannedWords.length === 0) return [true];
  const haystack = [parsed.subject, parsed.body, parsed.footer]
    .filter(Boolean)
    .join('\n');
  const hits = bannedWords.filter((w) =>
    new RegExp(`\\b${escapeRegex(w)}\\b`, 'i').test(haystack),
  );
  if (hits.length === 0) return [true];
  return [
    false,
    `commit message contains banned word(s): ${hits.join(', ')}. ` +
      `See BANNED_COMMIT_WORDS org var.`,
  ];
};

export default {
  extends: ['@commitlint/config-conventional'],
  plugins: [{ rules: { 'banned-words': bannedWordsRule } }],
  rules: {
    'banned-words': [2, 'always'],
  },
};
