import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // `public/` is static assets served as-is by Vite -- `public/phonon/`
  // (a vendored copy of the third-party phonon-visualization site, see
  // App.jsx's "Phonon visualizer" button) is a minified bundle, not this
  // project's source, and was never meant to be linted as if it were.
  globalIgnores(['dist', 'public']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
  {
    // TS/TSX gets the same React rules as the JS config above, plus
    // typescript-eslint's recommended (non-type-checked) rule set --
    // "recommendedTypeChecked" would need a `parserOptions.project` wired to
    // tsconfig.json and a much slower typed-lint pass, which is more setup
    // than tonight's deadline calls for for a first pass.
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // `any` is used deliberately in several spots (untyped third-party
      // libs, dynamic backend JSON) -- see the inline comments at each use
      // site rather than banning it project-wide.
      '@typescript-eslint/no-explicit-any': 'off',
      // Same ignore pattern as the plain-JS config above (kept for parity:
      // a handful of pre-existing dead helpers, e.g. `fmt`/`SettingsIcon`,
      // predate this migration -- see App.tsx; not this task's job to prune).
      '@typescript-eslint/no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
])
