# prompts/metrics.py

METRICS_EXTRACTION = """You are a document analyst. Analyze the document below and extract a precise writing intelligence profile that can be used to replicate HOW this brand writes — not WHAT it wrote.

CRITICAL RULE: You are extracting PATTERNS, not content. Never copy sentences, phrases, or specific words from the document. Every field must describe the writing mechanics and structural moves in abstract, reusable terms.

ON THE EXAMPLES BELOW: every "e.g." in this schema is there to show the SHAPE of an answer, not the subject matter of one. Several happen to be drawn from B2B marketing writing — client counts, ROI figures, audit names. That is incidental. This brand may be a television studio, a drinks company, a public body, a novelist, or anything else, and its authority, evidence and social proof may take completely different forms (viewership numbers, ingredient claims, awards, publication history) or may not exist at all. Describe what THIS document actually does. If a field has no counterpart in this brand's writing, say so plainly rather than inventing something to fill the shape of the example.
## OUTPUT REQUIREMENTS

Your output must be a JSON object that matches the schema below exactly. Every field must describe writing mechanics in abstract, reusable terms — never copy sentences or phrases from the document. For numeric fields (0.0–1.0), use the scale definitions below. For string fields, describe the pattern in one to two precise sentences.

## SCALE DEFINITIONS

For all numeric scores (0.0–1.0):
- `formality`: 0.0 = casual/colloquial → 1.0 = highly formal/academic
- `vocabulary_complexity`: 0.0 = simple everyday words → 1.0 = specialized/technical vocabulary
- `assertiveness`: 0.0 = heavily hedged, tentative → 1.0 = absolute, declarative, no qualifiers
- `urgency_level`: 0.0 = relaxed, no time pressure → 1.0 = urgent, act-now language
- `evidence_ratio`: 0.0 = all claims unsubstantiated → 1.0 = every claim backed by data/proof

Return ONLY a JSON object with no preamble or markdown. Use this schema:
{{
  "brand_name": "<extract the brand or company name from the document. If not found, return null>",
  "style": {{
    "avg_sentence_length": "<short|medium|long>",
    "sentence_complexity": "<simple|compound|complex|mixed>",
    "voice": "<active|passive|mixed>",
    "paragraph_length": "<short|medium|long>",
    "rhythm": "<punchy|flowing|dense|measured>",
    "formality": <score 0.0-1.0>,
    "vocabulary_complexity": <score 0.0-1.0>,
    "use_of_jargon": "<none|light|heavy|domain_specific>",
    "use_of_bullets_or_lists": "<none|occasional|frequent>",
    "use_of_formatting": "<none|light|heavy>",
    "numerical_density": "<low|medium|high>",
    "pronoun_pattern": "<describe the dominant pronoun strategy — which pronouns are used, in what contexts, and how the brand positions itself relative to the reader. e.g. 'first-person plural dominant; brand name used as subject in authority claims; second-person for diagnosing reader problems'>",
    "punctuation_habits": "<describe distinctive punctuation choices — em dashes, semicolons, exclamation marks, ellipses, colons. Note frequency and function. e.g. 'heavy em-dash usage for asides; no semicolons; rare exclamation marks'>",
    "capitalization_style": "<describe the letter-casing convention this document actually uses — one of: 'standard sentence case', 'all lowercase (including sentence starts and the brand/product name)', 'all uppercase', 'title case headlines', or 'mixed/stylized' with a one-phrase description of the mix. Base this ONLY on what the document itself does, never assume standard case by default.>",
    "question_usage": "<describe how and where questions appear — rhetorical, Socratic, direct, or none. Frequency, placement (section openings, endings, mid-paragraph), and whether they are answered. e.g. 'rhetorical questions at section endings; always followed by the brand's answer; 1-2 per piece'>",
    "metaphor_usage": "<describe metaphor/analogy patterns — source domains the brand draws from, how metaphors are used (to explain processes, evoke emotions, simplify complexity), and what domains are avoided. Return 'none' if brand avoids metaphor>",
    "qualification_style": "<describe how the brand handles nuance and certainty — does it hedge ('might', 'could', 'perhaps'), qualify with data ('in 73% of cases'), qualify with experience ('in our experience'), or assert without qualification? Note what it avoids>",
    "mechanical_rules": {{
      "sentence_rhythm": "<describe the rhythm mechanics — e.g. 'alternates short declarative sentences (3-8 words) with longer explanatory ones (15-25 words); uses 1-2 fragment sentences per section for punch'>",
      "paragraph_constraints": "<describe paragraph limits — e.g. 'max 4 sentences per paragraph; opening paragraph is always 2-3 sentences; single-sentence paragraphs used for emphasis'>",
      "heading_format": "<describe heading conventions — e.g. 'numbered bold headings for main sections; no bullet points as primary structure'>",
      "evidence_anchoring": "<describe how claims are grounded — e.g. 'every major claim is anchored to a specific number, client count, timeframe, or measurable outcome; vague assertions are never left unanchored'>"
    }}
  }},
  "intellectual_patterns": {{
    "diagnostic_style": "<describe HOW the brand names problems — e.g. 'frames failures as intention gaps not knowledge gaps: brands confuse X with Y, brands perform Z rather than live it'>",
    "value_hierarchy": "<describe the brand's ranked priorities — e.g. 'consistency ranked above creativity; authenticity ranked above performance; long-term relationship ranked above short-term transaction'>",
    "reframing_moves": "<describe HOW the brand redefines concepts — e.g. 'takes a commonly accepted term and splits it into what people think it means vs. what it actually means; uses X is not Y, it is Z structure'>",
    "authority_source": "<describe what makes the brand credible — e.g. 'grounds authority in volume of client experience with specific numbers, and in duration of practice with specific timeframes'>",
    "argument_structure": "<describe the intellectual arc — e.g. 'opens with relatable observation → names the hidden flaw in common behavior → states the brand's diagnostic → prescribes the value-hierarchy-aligned solution → closes with outcome'>",
    "thinking_templates": [
      "<describe 2-4 abstract sentence structures the brand uses as intellectual moves — describe the MOVE, not the words. e.g. 'opens section with a pattern observation using present tense plural subject', 'pivots with a contrast using But here is the + uncomfortable truth + authority anchor', 'closes section with a rhetorical question that mirrors the opening problem'>"
    ]
  }},
  "tone": {{
    "register": "<formal|semi-formal|conversational>",
    "emotional_quality": ["<e.g. confident, cautious, persuasive, empathetic>"],
    "reader_relationship": "<authoritative|collaborative|deferential|intimate|transactional>",
    "hedging_frequency": "<low|medium|high>",
    "assertiveness": <score 0.0-1.0>,
    "urgency_level": <score 0.0-1.0>
  }},
  "structure": {{
    "argumentation_style": "<deductive|inductive|problem_solution|storytelling|mixed>",
    "evidence_ratio": <score 0.0-1.0>,
    "transition_density": "<low|medium|high>",
    "front_loads_conclusions": <true|false>,
    "opening_pattern": "<describe HOW openings are written — the structural move, not the words. e.g. 'opens with a universally relatable brand behavior stated as present-tense observation, immediately followed by an uncomfortable truth pivot anchored to brand experience and client count, closed with a two-part reframe that splits what brands think they are doing from what they are actually doing'>",
    "closing_pattern": "<describe HOW closings are written — the structural move, not the words. e.g. 'closes with brand methodology restatement in one sentence, followed by two parallel short sentences contrasting what the work does not do vs. what it does, ending with a soft CTA phrased as an inviting question'>",
    "section_pattern": "<describe how body sections are structured — the pattern, not the content>",
    "narrative_arc": "<describe the overall flow — e.g. 'hook → diagnosis → brand experience proof → actionable insight list → CTA'>"
  }},
  "persuasion": {{
    "primary_appeal": "<logos|ethos|pathos|mixed|none>",
    "social_proof_usage": "<none|light|heavy>",
    "social_proof_pattern": "<describe HOW social proof is used — e.g. 'anchors credibility claims to specific client volume numbers and measurable outcome percentages; never uses vague terms like many clients'>",
    "call_to_action_pattern": "<none|single|repeated|pervasive>",
    "cta_pattern": "<describe HOW CTAs are written — the structural move. e.g. 'single soft CTA as an inviting question at the very end; mirrors the problem named in the opening; uses first-person plural invitation verb'>"
  }},
  "asset_bank": {{
    "social_proof_claims": [
      "<copy each specific social proof claim VERBATIM from the document — exact wording, exact numbers. These are factual claims the brand makes about its own experience. e.g. 'we've built brand-aligned content frameworks for 84 B2B SaaS companies and found 250% higher ROI'. Include ONLY claims that state a specific number, client count, percentage, or measurable outcome tied to the brand's direct experience. Do NOT include generic industry statistics.>"
    ],
    "named_frameworks": [
      "<copy each named methodology, audit, or framework the brand owns VERBATIM — e.g. 'Vantage Pricing Audit', 'Content Audit', 'Deck Audit'. Include only brand-owned names, not generic terms.>"
    ],
    "stated_values": [
      "<copy each stated value or belief VERBATIM as it appears — e.g. 'Consistency beats creativity', 'Authenticity is non-negotiable'. Short declarative statements only.>"
    ],
    "financial_targets": [
      "<copy each specific financial target, projection, investment figure, or quantified operational outcome VERBATIM from the document — exact wording, exact numbers, exact timeframes. These are the brand's own stated numeric targets and projections. e.g. 'targets $55M in annualized run-rate savings by Year 3', 'a 35% reduction in unplanned downtime', '$16M investment for Phase 1 and Phase 2', '450,000 tonnes of CO2 captured annually'. Include dollar amounts, percentages, MW capacities, tonnes, timeframes (Year 1, Months 1-6, etc.), and any other quantified target or projection tied to a specific program or initiative. Do NOT include generic industry statistics.>"
    ]
  }},
  "signature_constructions": [
    "<describe distinctive WRITING CONSTRUCTIONS — the move, not the phrase. e.g. 'uses a two-part contrast sentence where part 1 names what content does and part 2 names what it actually is', 'opens sections with a we + past tense + volume anchor sentence to establish authority before making a claim'>"
  ],
  "section_patterns": {{
    "opening_move": "<describe the opening structural move in abstract terms>",
    "list_introduction_move": "<describe how the brand introduces lists or key points — e.g. 'introduces numbered insights with a Here is what we learned after + timeframe + context phrase'>",
    "closing_move": "<describe the closing structural move in abstract terms>"
  }},
  "generation_instructions": "<write a concrete, pattern-focused brief on HOW to write in this style. Focus entirely on structural moves and writing mechanics. Name what to replicate as patterns (e.g. open with a relatable observation then pivot to uncomfortable truth, anchor every claim to a number). Name what to avoid as anti-patterns (e.g. never open with a rhetorical question, never leave a claim unanchored, never hedge with might or could). Do NOT reference specific sentences or phrases from the source document. Include at least 8 DO instructions and 5 DON'T instructions.>"
}}

Content type: {content_type}
Document:
{document}"""


METRICS_SYNTHESIS_SINGLE = """You are a brand intelligence analyst. You have been given 1 extracted writing intelligence profile from a document belonging to this brand.

Your job is to convert this profile into a precise brand writing intelligence brief that a content writer can use to produce ORIGINAL content that is structurally and tonally indistinguishable from this brand's voice.

CRITICAL RULE: This brief must describe HOW to write, never WHAT to write. Do not include any specific sentences, phrases, or words from the source documents. Every section must describe writing patterns, structural moves, and intellectual mechanics in abstract, reusable terms. A writer should be able to use this brief to write about ANY topic in this brand's voice.

Rules:
- State patterns with confidence where the profile is clear and specific.
- Note "Needs more samples" only where the profile is genuinely ambiguous or contradictory — not merely because you have a single document.
- A single well-written document can reveal sentence rhythm, tone, structure, pronoun patterns, and intellectual moves with high confidence.
- BRAND ASSET BANK is a required section — aggregate all social_proof_claims, named_frameworks, and stated_values from all profiles. Preserve exact wording.
- For social_proof_claims confidence: mark HIGH if the TYPE of claim (e.g. 'We've [action] for X clients and seen Y%') is a clear brand habit in this document. The specific numbers are always unique — confidence refers to whether this claim STYLE is an established brand pattern, not whether the exact number recurs.
- OPENING PATTERN and CLOSING PATTERN are the most critical sections — describe the structural moves precisely so any writer can execute them on any topic.
- GENERATION INSTRUCTIONS must be entirely pattern-based — no content references, no quoted phrases, only structural and tonal mechanics a writer can apply to any topic.
- Extract the brand name from the profile and include it explicitly.

Business: {business_id}
Content type: {content_type}

Extracted profile:
{profiles}

Return your synthesis as a well-structured plain text brand writing intelligence brief. Use clear section headers starting with #. Do not return JSON.

"""


METRICS_SYNTHESIS = """You are a brand intelligence analyst. You have been given {total_documents} extracted writing intelligence profiles from documents belonging to the same brand.

Your job is to synthesize these profiles into a single, precise brand writing intelligence brief that a content writer can use to produce ORIGINAL content that is structurally and tonally indistinguishable from this brand's voice.

CRITICAL RULE: This brief must describe HOW to write, never WHAT to write. Do not include any specific sentences, phrases, or words from the source documents. Every section must describe writing patterns, structural moves, and intellectual mechanics in abstract, reusable terms. A writer should be able to use this brief to write about ANY topic in this brand's voice.

Rules:
- Where profiles agree, state the pattern confidently and concisely.
- Where profiles contradict, identify the underlying reason and give practical guidance.
- Profiles with a higher weight (score_weight) represent validated high-quality content and should carry more influence.
- OPENING PATTERN and CLOSING PATTERN are the most critical sections — describe the structural moves precisely so any writer can execute them on any topic.
- GENERATION INSTRUCTIONS must be entirely pattern-based — no content references, no quoted phrases, only structural and tonal mechanics a writer can apply to any topic.
- Do not invent patterns not present in the source profiles. State "Insufficient data" where needed.
- Extract the brand name from the profiles and include it explicitly.
- BRAND ASSET BANK is a required section — aggregate all social_proof_claims, named_frameworks, and stated_values from all profiles. Preserve exact wording.

CRITICAL — TWO-LEVEL CONFIDENCE MODEL for the BRAND ASSET BANK:
  PATTERN confidence = how consistently this TYPE of claim is used across ALL profiles (e.g. if every profile contains a 'We've [action] for X clients and seen Y%' style claim, the PATTERN is HIGH confidence even if each claim's specific numbers are unique per document).
  DATA confidence = whether this SPECIFIC claim's exact numbers appear in multiple profiles (they rarely will, since each document describes different client work).
  For social_proof_claims: mark PATTERN confidence HIGH if this type of evidence appears in 3+ profiles. Mark it LOW only if this is an isolated, unusual claim type not mirrored in other documents.
  Do NOT mark every claim LOW simply because its specific numbers are unique to one document. The numbers are always unique — what matters is whether the brand consistently makes this type of claim.


Business: {business_id}
Content type: {content_type}

Extracted profiles (ordered oldest to newest):
{profiles}

Return your synthesis as a well-structured plain text brand writing intelligence brief. Use clear section headers starting with #. Do not return JSON.

"""


METRICS_SYNTHESIS_TEMPLATE = """Structure your output exactly as follows:

# BRAND NAME
[The brand name extracted from documents, written as plain text on this line — do NOT put the name in the "# BRAND NAME" header itself. If not found: Not extracted — inject manually.]

# BRAND ASSET BANK
[Aggregate the asset_bank entries from all profiles. Deduplicate. CRITICAL RULE: DO NOT list every single claim. For social_proof_claims: list a maximum of the TOP 15 most frequent or highest-impact claims. For named_frameworks and stated_values: list a maximum of the TOP 5 most important unique entries. For financial_targets: list ALL unique targets — do not cap these, as every distinct figure is a permitted number the writer may use.]

SOCIAL PROOF CLAIMS:
- [VERBATIM claim] (pattern confidence: HIGH/LOW — HIGH means this STYLE of claim appears consistently across 3+ profiles; LOW means it is an isolated or unusual claim type)

NAMED FRAMEWORKS & METHODOLOGIES:
- [VERBATIM framework name] (appears in N/M profiles)

STATED VALUES & BELIEFS:
- [VERBATIM value statement] (appears in N/M profiles)

FINANCIAL TARGETS & PROJECTIONS:
- [VERBATIM financial target, projection, or quantified outcome — exact numbers, percentages, dollar amounts, timeframes] (appears in N/M profiles)

# BRAND VOICE OVERVIEW
[2-3 sentences describing the brand's core writing identity in terms of HOW it writes — register, authority style, reader relationship, and intellectual stance. No content references.]

# INTELLECTUAL PATTERNS

DIAGNOSTIC STYLE:
[How this brand names and frames problems — the intellectual move, not examples. e.g. "frames failures as intention gaps: the brand consistently diagnoses problems as confusions between two things rather than lack of knowledge or resources"]

VALUE HIERARCHY:
[What the brand ranks above what, and how this shapes prescriptions — e.g. "ranks consistency above creativity, authenticity above performance; prescriptions always reflect this order"]

REFRAMING MOVES:
[How the brand redefines concepts — the structural move. e.g. "takes widely accepted terms and splits them: names what people think X means, then names what X actually is in the brand's framework"]

AUTHORITY SOURCE:
[How the brand establishes credibility — the pattern, not the claims. e.g. "grounds authority in specific volume of experience with numbers, and in duration of practice with timeframes; never makes unanchored credibility claims"]

ARGUMENT STRUCTURE:
[The intellectual arc the brand uses — described as a sequence of moves. e.g. "relatable observation → hidden flaw diagnosis → brand authority anchor → value-hierarchy prescription → measurable outcome"]

THINKING TEMPLATES:
[Describe 3-5 abstract sentence-level intellectual moves the brand makes. Describe the MOVE not the words:]
- [Move 1: e.g. "opens with a present-tense plural observation about common brand behavior that the reader will recognize in themselves"]
- [Move 2: e.g. "pivots with a contrast marker followed by an uncomfortable truth statement anchored to brand experience volume"]
- [Move 3: e.g. "closes sections with a cause-effect diagnosis using Why? Because [root cause] structure"]
- [Move 4: e.g. "introduces brand prescription with a The brands that win construction followed by the value-hierarchy-aligned behavior"]

# STYLE SIGNATURE
[Consolidated style mechanics. Describe patterns, not values:]

PRONOUN PATTERN:
[Dominant pronoun strategy and when each pronoun type is used — e.g. "first-person plural for authority claims, second-person for diagnosing reader problems, brand name as subject for institutional claims"]

PUNCTUATION HABITS:
[Distinctive punctuation choices — which marks are used, how frequently, and for what purpose. Which marks are avoided.]

CAPITALIZATION STYLE:
[State plainly which one applies, based on the actual profiles, not an assumption: "standard sentence case", "all lowercase", "all uppercase", "title case headlines", or "mixed/stylized" with a short description. If profiles disagree, say so and default to standard sentence case.]

QUESTION USAGE:
[How questions are used — type, frequency, placement, and whether they are answered.]

METAPHOR & ANALOGY:
[Source domains, how metaphors function in the writing, and which metaphor domains are avoided.]

QUALIFICATION STYLE:
[How certainty and nuance are expressed — what replaces hedging, how claims are qualified.]

MECHANICAL RULES:
- Sentence rhythm: [how rhythm is mechanically achieved — describe the alternation pattern]
- Paragraph constraints: [specific structural limits]
- Heading format: [exact format convention]
- Evidence anchoring: [the rule for how claims must be grounded]

# TONE SIGNATURE
[Consolidated tone mechanics — register, assertiveness level, hedging rules, reader relationship stance.]

# STRUCTURE SIGNATURE

OPENING PATTERN:
[Describe the structural sequence of moves used to open content — abstract and reusable for any topic. e.g. "Move 1: state a universally relatable brand behavior as a present-tense observation. Move 2: pivot with an uncomfortable truth marker anchored to brand experience. Move 3: execute a two-part reframe splitting what brands think they are doing from what they are actually doing."]

CLOSING PATTERN:
[Describe the structural sequence of moves used to close content — abstract and reusable for any topic. e.g. "Move 1: restate the brand's core methodology in one sentence using first-person plural. Move 2: execute two parallel short sentences contrasting what the work avoids vs. what it produces. Move 3: close with a soft CTA as an inviting question that mirrors the opening problem."]

SECTION PATTERN:
[Describe how body sections are structured — the move sequence, not content.]

NARRATIVE ARC:
[The standard flow described as a sequence of functional moves — e.g. "hook move → diagnosis move → authority anchor → insight list → CTA move"]

EVIDENCE PATTERN:
[The rule for how claims are grounded — describe the pattern not the examples.]

# SIGNATURE CONSTRUCTIONS
[Describe 4-6 distinctive writing constructions as abstract moves. Describe WHAT THE CONSTRUCTION DOES, not what it says:]
- [Construction 1: describe the move]
- [Construction 2: describe the move]

# GENERATION INSTRUCTIONS
DO:
- [Pattern-based instruction — minimum 8, all structural/tonal, no content references]

DON'T:
- [Anti-pattern instruction — minimum 5, all structural/tonal, no content references]

# CONFIDENCE ASSESSMENT
Well-established dimensions (consistent across 3+ profiles, HIGH confidence): [list them]
Dimensions needing more data (contradictions or only 1 profile): [list them with specific contradiction noted]

NOTE: A structural pattern (e.g. opening format, closing CTA, evidence anchoring, pronoun usage) that is CONSISTENT across all observed profiles is HIGH confidence even if specific content within that pattern varies per document. Only flag a dimension as LOW confidence if profiles genuinely contradict each other on that dimension, or if only 1 profile provided data on it with no corroboration."""