"""Enhanced prompt templates with Chain of Thought and Few-Shot examples."""

PROMPTS: dict[str, str] = {
    "eli5": """Think step-by-step: 1. Analyze audience (5-year-old). 2. Select a sensory, vivid analogy. 3. Draft explanation.

Explain {topic} like I'm 5: simple words, short sentences, easy analogy. Use sensory analogies (sights, sounds, tastes) to make it vivid and engaging. End with a simple, engaging question for the child.

Few-shot examples:
Topic: Gravity
Output: Gravity is like an invisible hug from the Earth! It keeps you from floating away into the sky like a balloon. When you jump, gravity pulls you back down - it's like the Earth saying "come back!" That's why when you drop a ball, it falls down instead of up. Can you feel gravity when you jump on your bed?

Topic: Rainbow
Output: A rainbow is like a magic smile in the sky made of colors! When the sun shines through raindrops, the light breaks into all the pretty colors you see - red, orange, yellow, green, blue, and purple. It's like when you blow bubbles and see shiny colors dancing on them! Have you ever tried to touch a rainbow?

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "eli10": """Think step-by-step: 1. Analyze audience (10-year-old). 2. Select relatable examples. 3. Draft explanation.

Explain {topic} for a 10-year-old: basic terms, clear examples from everyday life. Include one 'Did you know?' fun fact to spark curiosity.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "eli12": """Think step-by-step: 1. Analyze audience (12-year-old). 2. Balance simplicity with some technical depth. 3. Draft explanation.

Explain {topic} for a 12-year-old: some technical terms with clear definitions, real-world examples they can relate to.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "eli15": """Think step-by-step: 1. Analyze audience (15-year-old). 2. Introduce key concepts with appropriate depth. 3. Draft explanation.

Explain {topic} for a 15-year-old: more depth, key concepts explained clearly, connections to broader ideas.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "meme": """Explain {topic} as a funny one-liner meme with relatable analogy. Make it punchy and shareable.

Output in plain text only.""",


    "classic60": """Think step-by-step: 1. Analyze audience (60+ years). 2. Select classic, familiar metaphors. 3. Draft at slower pace.

Explain {topic} to someone who's 60 or older. Use classic metaphors they'll recognize - like balancing a checkbook, reading the newspaper, looking up shows in a TV Guide, or tuning a radio dial. Take your time with the explanation, using clear language and familiar examples from everyday life.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "gentle70": """Think step-by-step: 1. Analyze audience (70+ years). 2. Choose deeply familiar analogies. 3. Craft patient, reassuring explanation.

Explain {topic} to someone who's 70 or older with extreme patience and warmth. Use familiar examples like a rotary phone, drive-in movies, a party line, or mailing a letter. No technical jargon whatsoever - keep everything in plain, everyday language. Include reassuring phrases to make them feel comfortable. There's no hurry, take all the time needed.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",

    "warm80": """Think step-by-step: 1. Analyze audience (80+ years). 2. Select ultra-basic, timeless analogies. 3. Craft warmest possible explanation.

Explain {topic} to someone who's 80 or older with the warmest, most patient tone possible. Use ultra-basic analogies from timeless activities like baking bread, tending a garden, knitting, or sitting on the porch. Keep everything simple and comforting. No rush at all - we have all the time in the world.

Output in plain text only, no markdown, no bolding, no headers. Just paragraphs. CRITICAL: Do not output your thinking process, "Thought:", or any <think> blocks. Output ONLY the final explanation.""",
}

JUDGE_PROMPT = """Rate these responses for "{topic}" on coherence (1-5), accuracy (1-5), conciseness (1-5).
{responses}
Output JSON: {{"best": 0|1, "reason": "brief"}}"""

# Model configs for Groq
FREE_MODELS = ["llama-3.1-8b-instant"]
PREMIUM_MODELS = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768"]
JUDGE_MODEL = "llama-3.3-70b-versatile"
FAST_MODEL = "llama-3.1-8b-instant"

TECHNICAL_DEPTH_PROMPT = """
You are an expert academic researcher and tutor.

Provided Search Context (real-time web results):
{search_context}

Optional Quote (use if relevant for engagement):
{quote_text}

Topic: {topic}

Guidelines:
- Synthesize ONLY from the provided context + your knowledge. NEVER fabricate facts or sources.
- DO NOT use inline numeric citations (e.g., [1], [2]). Keep the prose clean and professional.
- List all sources at the very end in a dedicated "### Sources" section.
- Structure strictly with Rich Markdown:
  - Use ## and ### for clear section hierarchy.
  - Use **bold** for key terms and *italics* for emphasis.
  - Use `inline code` for technical identifiers or variables.
  - Convert all reference URLs into clickable [Link Title](URL) format.
  - Use unordered lists (-) for features and ordered lists (1.) for steps.
  - Ensure double-line breaks between paragraphs and sections for maximum readability.
  - Add horizontal rules (---) between major sections (Summary, Deep Dive, Diagrams).
- Detailed Structure:
  1. **Executive Summary** (Comprehensive 4–6 sentence overview)
  2. ---
  3. **Technical Deep Dive** (Detailed explanation, covering architecture, theory, and implementation)
  4. ---
  5. **Key Mechanics / Architecture / Process** (use Mermaid if applicable)
  6. ---
  7. **Sources** (A clean list of titled hyperlinks: - [Title](URL))
- If explaining ANY process, system, workflow, hierarchy, sequence → MUST output valid Mermaid code in:
  ```mermaid
  graph TD
  ...
  ```
  *Fallback: If a diagram is too complex or likely to fail, provide a clear ASCII-art alternative or structured detail.*
- Use graduate-level language but explain complex jargon.
- Aim for a comprehensive length of ~1000 words. Do not truncate early.
- If a quote fits naturally, weave it in as a blockquote.

CRITICAL: Base everything on context. Flag if context is insufficient. Flush out the details to ensure a professional, high-quality technical document.
"""
