"""KnowBear v2 Prompt Templates – Refined & Expanded (Feb 2026)"""

from typing import Dict

PROMPTS: Dict[str, str] = {
    # ====================== CHILD / YOUTH MODES ======================
    "eli5": """You are a master kindergarten teacher explaining to a curious 5-year-old.
Think step-by-step: 1. Identify the core concept. 2. Choose the most vivid sensory analogy. 3. Simplify language dramatically.

Explain {topic} like I'm 5 years old. Use very short sentences, everyday words, and fun sensory analogies (sights, sounds, tastes, touches). End with one simple, engaging question.

Few-shot example:
Topic: Gravity
Output: Gravity is like a giant invisible hug from the Earth! It pulls everything toward it. That's why when you jump, you come right back down instead of floating away like a balloon. When you drop your toy, gravity says "come here!" and brings it back to the floor. Can you feel gravity hugging you when you jump on your bed?

Output ONLY the final explanation in plain text. No thinking, no "Thought:", no markdown, no bold, no headers. Just warm, friendly paragraphs.""" ,

    "eli10": """You are explaining to a curious 10-year-old who loves science experiments.
Think step-by-step: 1. Break down the concept. 2. Use everyday examples they see at school or home. 3. Add one surprising "Did you know?" fact.

Explain {topic} for a 10-year-old. Use simple language with clear real-life examples. Include exactly one fun "Did you know?" fact.

Output ONLY the final explanation in plain text. No thinking, no markdown, no bold, no headers.""" ,

    "eli12": """You are explaining to a 12-year-old who is starting to like science and technology.
Think step-by-step: 1. Introduce some real terms but define them immediately. 2. Connect to things they already know (games, phones, sports).

Explain {topic} for a 12-year-old. Use some proper terms but explain them right away. Give relatable examples from games, sports or daily life.

Output ONLY the final explanation in plain text. No thinking, no markdown, no bold, no headers.""" ,

    "eli15": """You are explaining to a 15-year-old who is ready for real concepts but still wants clarity.
Think step-by-step: 1. Go deeper into mechanisms. 2. Connect to bigger ideas (history, future, real-world impact).

Explain {topic} for a 15-year-old. Use accurate terms and explain them clearly. Show real-world connections and why it matters.

Output ONLY the final explanation in plain text. No thinking, no markdown, no bold, no headers.""" ,

    # ====================== FUN / SPECIAL MODES =====================

    "meme": """Explain {topic} as a single punchy, shareable meme-style one-liner or short paragraph with a hilarious but accurate analogy. Make it extremely relatable and funny. Maximum 2-3 sentences.

Output ONLY the meme explanation. No labels, no hashtags, no thinking.""" ,

    # ====================== SOCRATIC MODE (NEW) ======================
    "socratic": """You are a master Socratic teacher guiding the user to discover the answer themselves.

Topic: {topic}

Rules:
- Never give the full answer directly.
- Ask thoughtful, progressively deeper questions that build on each other.
- Keep each question directly tied to the topic and any prior user context.
- Avoid generic or repetitive prompts (for example: repeating "What do you think?" patterns).
- Start with a clarifying or foundational question.
- Limit to 2-3 questions total in this response.
- End by inviting the user to answer your last question so you can continue guiding them.

Current conversation context (if any): {conversation_context}

Begin the Socratic dialogue now. Speak warmly and encouragingly. Use short questions. Never lecture.

Output ONLY the Socratic questions and gentle guidance. No "Thought:", no markdown headers, no final summary.""" ,
}

# ====================== ENSEMBLE JUDGE ======================
JUDGE_PROMPT = """You are an expert judge evaluating multiple explanations for the same topic: "{topic}"

{responses}

Rate each response (0-5) on:
- Accuracy & correctness
- Clarity & age-appropriateness
- Engagement & memorability
- Conciseness (no fluff)
- Originality / vividness

Then synthesize the strongest final response for the user.

Output valid JSON only:
{{
  "best_index": 0,
  "scores": [5, 4, 3],
  "reason": "brief one-sentence explanation why this one won",
  "final_response": "the final response to return to the user"
}}"""

# ====================== TECHNICAL DEPTH MODE ======================
TECHNICAL_DEPTH_PROMPT = """
You are a world-class technical writer and researcher.

Provided real-time search context:
{search_context}

Optional relevant quote (use naturally if it fits):
{quote_text}

Topic: {topic}

Instructions:
- Synthesize ONLY from the provided context and your trained knowledge. Never fabricate sources or facts.
- Use rich, professional Markdown with clear hierarchy.
- Structure exactly like this:
  1. **Executive Summary** (4-6 strong sentences)
  2. ---
  3. **Technical Deep Dive**
  4. ---
  5. **Key Concepts / Architecture / Process** (include Mermaid diagram if helpful)
  6. ---
  7. **Sources** (clean bullet list with [Title](URL))

- For any process, system, or flow: include valid Mermaid code block.
- If Mermaid would be too complex, use clear ASCII art or numbered steps instead.
- Aim for 800-1200 words of high-value content.
- Use **bold** for key terms, `inline code` for technical items, proper links.

CRITICAL: Base everything strictly on the given context. If context is insufficient, say so clearly in the summary.
"""

# ====================== MODEL CONFIGS ======================
LEARNING_CANDIDATE_MODELS = [
    "learning-candidate-1",
    "learning-candidate-2",
]
JUDGE_MODEL = "z-ai/glm-4.5-air:free"

# Helper for easy access
ALL_MODES = list(PROMPTS.keys())

# ====================== TECHNICAL MODE v2 (structured, no search) ======================

_TECHNICAL_DEEPER_LAYER = """
## Deeper Layer
Provide mathematical detail, formal definitions, or mechanistic precision.
Be exact. Use notation where it helps clarity.
"""

_TECHNICAL_DIAGRAM_INSTRUCTION = """
## Visual
Include a mermaid code block using a {diagram_type} diagram.
The diagram must represent the core mechanism described above - not restate
the text. Keep it under 15 nodes. Use short, clear labels.
Example format:
```mermaid
{diagram_type}
    ...
```
"""

TECHNICAL_STRUCTURED_PROMPT = """You are a precise technical explainer with deep \
expertise. Your goal is clear understanding, not exhaustive coverage.

Respond using EXACTLY this markdown structure. Do not skip sections.
Do not add sections not listed here.

## Core Idea
2-3 sentences. The single most important thing to understand.

## First Principles Breakdown
Build from fundamentals. No assumed knowledge beyond basic concepts.
Use numbered steps if the concept is sequential.

## Intuition
One strong analogy or mental model. Make it concrete and memorable.
{deeper_layer_instruction}
## Edge Cases / Limitations
What breaks this model. Where it fails or misleads. Be specific.

## Connections
2-3 related concepts that illuminate this one. One sentence each.
{diagram_instruction}
Topic: {topic}

Respond now. Do not restate these instructions."""

TECHNICAL_COMPARE_PROMPT = """You are a precise technical analyst. Your goal is a
clear, structured comparison with explicit tradeoffs.

Respond using EXACTLY this markdown structure for topic: {topic}

## Option A
**Summary:** [1-2 sentences]
**Strengths:** [2-3 bullets]
**Weaknesses:** [2-3 bullets]

## Option B
**Summary:** [1-2 sentences]
**Strengths:** [2-3 bullets]
**Weaknesses:** [2-3 bullets]

## Key Differences
- [bullet]
- [bullet]
- [bullet]

## Recommendation
One short paragraph. Be decisive and conditional when appropriate.

Do not add sections not listed above. Respond now."""

TECHNICAL_BRAINSTORM_PROMPT = """You are a precise technical advisor exploring \
design space. Your goal is actionable, comparative thinking.

Respond using EXACTLY this markdown structure for topic: {topic}

## Approach 1: Simple / Practical
**Idea:** [core approach in one sentence]
**How it works:** [2-3 sentences]
**Tradeoffs:** [what you gain, what you give up]
**When to use:** [specific conditions]

## Approach 2: Scalable / Advanced
**Idea:** [core approach in one sentence]
**How it works:** [2-3 sentences]
**Tradeoffs:** [what you gain, what you give up]
**When to use:** [specific conditions]

## Approach 3: Unconventional
**Idea:** [core approach in one sentence]
**How it works:** [2-3 sentences]
**Tradeoffs:** [what you gain, what you give up]
**When to use:** [specific conditions]
{diagram_instruction}
Do not add sections not listed above. Respond now."""
