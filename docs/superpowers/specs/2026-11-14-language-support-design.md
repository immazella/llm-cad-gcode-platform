# Turkish / English language support

User approved: selectable Turkish and English for the application UI, new assistant replies, validation explanations, persisted preference, tests and push.

## Design
Use explicit local translation catalogs and React locale context, not DOM rewriting or paid translation. Language selector in top bar; persist `tr` or `en` in localStorage; Turkish fallback preserves existing users. Send `Accept-Language` on API requests and `language` on chat requests. Backend normalizes locale request-locally; never use process-global mutable language. Translate human-facing presentation fields, not G-code, numbers, IDs, profile IDs, filenames, user content, or stored chat history. New online assistant requests receive an explicit response-language instruction. Offline mode should explain its limited command grammar in the selected language and localize new replies. UI and backend validation/errors support both languages. MCP embedded viewer receives its own selector/catalog with compatible protocol identifiers.

## Approaches
Chosen: explicit catalogs with interpolation and request-local backend localization; testable without API calls. Rejected: browser/DOM auto-translation (rewrites user data, fragile React integration); external model translation (cost, latency, non-deterministic).

## Acceptance
Language switches without discarding model/workspace or chat; refresh preserves choice; every major screen and dialog has English/Turkish labels; generated code is byte-identical for either locale; old messages remain intact; validation findings and new responses follow locale; no live provider requests in tests; existing suite/build pass. Technical standards and user/machine supplied names remain unchanged. README documents language behavior and limitations honestly.
