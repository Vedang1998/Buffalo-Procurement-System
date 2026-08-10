Buffalo Procurement OS — Validation & Build Packet v2.1

Prepared: August 9, 2026
Purpose: Give a completely context-free AI reviewer, Replit Agent, Codex, Claude Code, or human engineer the same authoritative understanding of the Buffalo House Procurement OS.

Read order

1. 01_CANONICAL_SYSTEM_SPEC_v2_1.md — business/system authority. What we are building, why, every important workflow, strategy, dataset, identity rule, human-intelligence rule, safety guardrail and target operating experience.
2. 02_EXTERNAL_AI_REVIEW_PROTOCOL_v2_1.md — exact independent-review and red-team procedure for Claude/Gemini/Grok/other strong models.
3. 03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md — paste into Replit Agent after the existing v1.3 code is in the project.
4. 04_CODE_REVIEW_PLAYBOOK_v2_1.md — use with Codex/Claude Code/GitHub PR review as the independent engineering verification layer.
5. 05_CURRENT_BUILD_STATUS_v1_3.md — what already exists, what is only coded versus production-executed, and the immediate gates.
6. code/Buffalo_Procurement_OS_v1_3_Catalog_Sales_Foundation.zip — current code package.

Authority order

If documents conflict, use this order:

1. 01_CANONICAL_SYSTEM_SPEC_v2_1.md
2. machine-enforced config/rules.toml in the current codebase
3. 03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md for execution sequencing
4. 05_CURRENT_BUILD_STATUS_v1_3.md for current implementation state
5. older Master Plans / historical README / recovered workbooks only as historical evidence

External AI reviewers are advisory. A reviewer may recommend a change, but it does not become a system rule merely because the reviewer sounds confident. Material changes must be consciously accepted and then reflected in the canonical spec plus code/tests.

Critical orientation

• Shopify Sidekick is intentionally not part of the permanent architecture.
• There is no mandatory runtime LLM.
• Replit is the intended permanent application home unless a new review identifies a materially superior option.
• Shopify Variant ID is canonical product identity.
• Supplier SKU is supplier mapping evidence, not canonical identity.
• Real PO reliance remains fail-closed behind readiness gates.
• Human intelligence such as gift packs, combos, assortment exceptions, one-bottle policies, events, temporary price knowledge, supplier mappings and review decisions must live in structured data/code — never only in chat memory.

Fast-build philosophy

Move extremely fast on coding, scaffolding, testing, UI and automation. Move deliberately on identity merges, supplier mappings, BT/CS interpretation, price promotion and real-money PO activation. AI speed is used to reduce engineering time, not to eliminate financial controls.