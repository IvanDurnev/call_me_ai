Browser dependencies vendored from the official npm registry:

- Marked 18.1.3: `marked/lib/marked.umd.js` (MIT).
- DOMPurify 3.4.17: `dompurify/dist/purify.min.js` (Apache-2.0 OR MPL-2.0).

Keep the license files when updating these assets. Chat Markdown is parsed by
Marked and sanitized by DOMPurify using an explicit tag and attribute allowlist.
Images and embedded content are excluded. Links allow only HTTP(S) and mailto.
The original source maps are included under the filenames referenced by each
browser build so developer tools can load them without 404 errors.
