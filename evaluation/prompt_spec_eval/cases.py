"""Eval test-case definitions for the PromptSpec system.

Each EvalCase bundles:
  - a PromptSpec (the config under test)
  - an optional RuntimeContext (injectors)
  - a human query sent to the LLM after the built system-prompt
  - a list of JudgeCriteria that describe what the judge LLM should check

The matrix deliberately exercises:
  - Every depth value (simple / accessible / technical / expert)
  - Every task value  (explain / compare / brainstorm / analyze / summarize)
  - Every reasoning value (direct / socratic / debate / guided)
  - Style variants (normal / concise / meme / academic)
  - Runtime injectors (search_context, conversation_context, diagram)
  - Known-incompatible combos (meme+expert, socratic+summarize) -> expect rejection
  - Edge cases (empty topic, unknown axes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import os
import random
import re

from api.prompt_engine.models import DiagramType, PromptSpec, RuntimeContext


@dataclass
class JudgeCriterion:
    """One observable requirement the judge must score 0.0–1.0."""

    name: str
    description: str
    weight: float = 1.0
    # Optional: a short string the raw response MUST or MUST NOT contain
    # (quick pre-filter before calling the LLM judge).
    must_contain: Optional[str] = None
    must_not_contain: Optional[str] = None
    # If True, failing this criterion fails the case regardless of average score.
    critical: bool = False


@dataclass
class EvalCase:
    """A single end-to-end eval scenario."""

    case_id: str
    spec: PromptSpec
    query: str  # The user message sent to the LLM after the system prompt
    criteria: list[JudgeCriterion]
    runtime: Optional[RuntimeContext] = None
    # If True, build_prompt_from_spec is expected to raise PromptSpecError.
    expect_spec_error: bool = False
    # Tag for grouping in reports.
    tags: list[str] = field(default_factory=list)
    # Per-case pass threshold for weighted average.
    pass_threshold: float = 0.7
    # Optional corpus chunk metadata used for logging failed chunks.
    corpus_chunks: list[dict] = field(default_factory=list)
    # Optional metadata for reporting (expected_behavior, category).
    metadata: dict = field(default_factory=dict)


def _depth_criteria(depth: str) -> list[JudgeCriterion]:
    if depth == "simple":
        return [
            JudgeCriterion(
                name="depth_appropriateness",
                description=(
                    "Uses plain language with no unexplained jargon. "
                    "Score 1.0 if a beginner can follow without lookup, 0.0 if technical language dominates."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="technical_correctness_level",
                description=(
                    "Basic technical facts are correct without misleading simplifications. "
                    "Score 1.0 if correct, 0.0 if inaccurate or misleading."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="explanation_granularity",
                description=(
                    "Stays at high-level concepts without deep implementation details. "
                    "Score 1.0 if only core concepts, 0.0 if dives into low-level mechanics."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="assumption_clarity",
                description=(
                    "States minimal assumptions or prerequisite knowledge explicitly. "
                    "Score 1.0 if assumptions are clear, 0.0 if hidden assumptions dominate."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="length_discipline",
                description=(
                    "Keeps the answer short and focused. "
                    "Score 1.0 if under 140 words, 0.5 if 140-220, 0.0 if longer."
                ),
                weight=0.75,
            ),
        ]
    if depth == "accessible":
        return [
            JudgeCriterion(
                name="depth_appropriateness",
                description=(
                    "Explains key terms as they appear and avoids expert-only shortcuts. "
                    "Score 1.0 if terms are defined or analogized, 0.0 if jargon is left unexplained."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="technical_correctness_level",
                description=(
                    "Technical statements are accurate at an introductory level. "
                    "Score 1.0 if correct, 0.0 if inaccurate or misleading."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="explanation_granularity",
                description=(
                    "Balances conceptual explanation with one or two mechanism details. "
                    "Score 1.0 if balanced, 0.0 if too shallow or too deep."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="assumption_clarity",
                description=(
                    "States or implies a reasonable baseline (e.g. basic CS familiarity). "
                    "Score 1.0 if assumptions are clear, 0.0 if the level is ambiguous."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="concrete_example",
                description=(
                    "Includes a short example or scenario to ground the concept. "
                    "Score 1.0 if a concrete example appears, 0.0 if none."
                ),
                weight=0.75,
            ),
        ]
    if depth == "technical":
        return [
            JudgeCriterion(
                name="depth_appropriateness",
                description=(
                    "Includes mechanism-level details suitable for engineers. "
                    "Score 1.0 if mechanism details are present, 0.0 if only high-level summaries."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="technical_correctness_level",
                description=(
                    "All technical claims are correct and internally consistent. "
                    "Score 1.0 if correct, 0.0 if incorrect or contradictory."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="terminology_precision",
                description=(
                    "Uses correct domain terms with precision. "
                    "Score 1.0 if key terms are used correctly, 0.0 if misused."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="edge_cases_or_limits",
                description=(
                    "Mentions at least one limitation, edge case, or constraint. "
                    "Score 1.0 if present, 0.0 if absent."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="explanation_granularity",
                description=(
                    "Provides details at a component or step level (not just summary). "
                    "Score 1.0 if step/component detail present, 0.0 if absent."
                ),
                weight=0.75,
            ),
        ]
    if depth == "expert":
        return [
            JudgeCriterion(
                name="depth_appropriateness",
                description=(
                    "Uses advanced framing suitable for expert readers. "
                    "Score 1.0 if expert-level framing present, 0.0 if only high-level summary."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="technical_correctness_level",
                description=(
                    "All technical claims are correct and nuanced. "
                    "Score 1.0 if correct, 0.0 if incorrect or oversimplified."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="tradeoff_analysis",
                description=(
                    "Explicitly names multiple tradeoffs with reasoning. "
                    "Score 1.0 if >= 2 tradeoffs, 0.5 if 1, 0.0 if none."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="formalism_or_first_principles",
                description=(
                    "References formal properties, definitions, or first-principles reasoning. "
                    "Score 1.0 if present, 0.0 if absent."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="open_questions_or_limits",
                description=(
                    "Mentions an open question, research debate, or known limitation. "
                    "Score 1.0 if present, 0.0 if absent."
                ),
                weight=0.75,
            ),
        ]
    return []


def _task_criteria(task: str) -> list[JudgeCriterion]:
    if task == "explain":
        return [
            JudgeCriterion(
                name="task_adherence",
                description=(
                    "Provides an explanation rather than a list of options or a debate. "
                    "Score 1.0 if clearly explanatory, 0.0 if primarily another task type."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="core_idea_first",
                description=(
                    "Opens with the core idea in the first 1-2 sentences. "
                    "Score 1.0 if the core idea appears early, 0.0 if buried."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="logical_progression",
                description=(
                    "Moves from overview to details in a clear order. "
                    "Score 1.0 if the flow is structured, 0.0 if it is disorganized."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="no_comparison_structure",
                description=(
                    "Does not present side-by-side options or compare alternatives. "
                    "Score 1.0 if purely explanatory, 0.0 if comparison structure appears."
                ),
                weight=0.5,
                must_not_contain="Option A",
            ),
        ]
    if task == "compare":
        return [
            JudgeCriterion(
                name="task_adherence",
                description=(
                    "Compares at least two options explicitly. "
                    "Score 1.0 if comparison is explicit, 0.0 if not."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="comparison_balance",
                description=(
                    "Presents pros and cons for each option. "
                    "Score 1.0 if both sides addressed, 0.0 if one-sided."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="comparison_dimensions",
                description=(
                    "Uses multiple comparison dimensions (e.g. performance, complexity, cost). "
                    "Score 1.0 if >= 2 dimensions, 0.5 if 1, 0.0 if none."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="recommendation_with_context",
                description=(
                    "Provides a conditional recommendation tied to context. "
                    "Score 1.0 if recommendation is contextual, 0.0 if missing."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="tradeoff_specificity",
                description=(
                    "Tradeoffs are specific and actionable. "
                    "Score 1.0 if >= 2 actionable tradeoffs, 0.5 if 1, 0.0 if none."
                ),
                weight=0.75,
            ),
        ]
    if task == "brainstorm":
        return [
            JudgeCriterion(
                name="task_adherence",
                description=(
                    "Provides multiple ideas instead of a single solution. "
                    "Score 1.0 if multiple ideas, 0.0 if single-solution."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="idea_diversity",
                description=(
                    "Presents at least 4 distinct ideas. "
                    "Score 1.0 if >= 4, 0.5 if 2-3, 0.0 if 1."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="idea_distinctness",
                description=(
                    "Ideas are not minor variations of the same theme. "
                    "Score 1.0 if clearly distinct, 0.0 if repetitive."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="range_of_levers",
                description=(
                    "Covers different levers (process, tooling, architecture, policy). "
                    "Score 1.0 if >= 2 levers, 0.5 if 1, 0.0 if none."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="no_single_answer_structure",
                description=(
                    "Does not collapse into a single recommendation. "
                    "Score 1.0 if multi-option format maintained, 0.0 if it picks one answer."
                ),
                weight=0.75,
                critical=True,
            ),
        ]
    if task == "analyze":
        return [
            JudgeCriterion(
                name="task_adherence",
                description=(
                    "Provides analysis rather than a single recommendation. "
                    "Score 1.0 if analytical, 0.0 if only prescriptive."
                ),
                weight=1.5,
                critical=True,
            ),
            JudgeCriterion(
                name="risk_coverage",
                description=(
                    "Covers multiple risk dimensions (e.g. correctness, performance, cost). "
                    "Score 1.0 if >= 3 dimensions, 0.5 if 2, 0.0 if 1 or less."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="causal_reasoning",
                description=(
                    "Explains why risks occur, not just what they are. "
                    "Score 1.0 if causal explanations present, 0.0 if only lists."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="tradeoff_discussion",
                description=(
                    "Discusses at least one tradeoff or tension. "
                    "Score 1.0 if present, 0.0 if absent."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="mitigation_consideration",
                description=(
                    "Mentions mitigation strategies or controls. "
                    "Score 1.0 if present, 0.0 if absent."
                ),
                weight=0.75,
            ),
        ]
    if task == "summarize":
        return [
            JudgeCriterion(
                name="task_adherence",
                description=(
                    "Provides a summary, not a deep explanation or debate. "
                    "Score 1.0 if summary-focused, 0.0 otherwise."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="brevity_vs_completeness",
                description=(
                    "Balances brevity with coverage of essentials. "
                    "Score 1.0 if under 120 words and complete, 0.5 if 120-180, 0.0 if longer."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="key_points_coverage",
                description=(
                    "Covers all major points implied by the query. "
                    "Score 1.0 if all major points included, 0.0 if key points missing."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="no_new_information",
                description=(
                    "Does not introduce novel facts beyond the main points. "
                    "Score 1.0 if no new facts, 0.0 if new tangents appear."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="terminology_inclusion",
                description=(
                    "Includes key terms from the topic. "
                    "Score 1.0 if key terms appear, 0.0 if absent."
                ),
                weight=0.75,
            ),
        ]
    return []


def _reasoning_criteria(reasoning: str) -> list[JudgeCriterion]:
    if reasoning == "direct":
        return [
            JudgeCriterion(
                name="reasoning_structure",
                description=(
                    "Provides a direct answer with a clear structure. "
                    "Score 1.0 if direct and structured, 0.0 if indirect or evasive."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="no_reflective_question",
                description=(
                    "Does not end with a reflective or Socratic question. "
                    "Score 1.0 if no question at the end, 0.0 if it ends with a question."
                ),
                weight=0.75,
                critical=True,
            ),
            JudgeCriterion(
                name="minimal_meta",
                description=(
                    "Avoids unnecessary meta-commentary (e.g. 'I will now explain'). "
                    "Score 1.0 if no meta, 0.0 if frequent meta commentary."
                ),
                weight=0.5,
            ),
        ]
    if reasoning == "socratic":
        return [
            JudgeCriterion(
                name="socratic_questioning_quality",
                description=(
                    "Asks a focused, targeted question that advances understanding. "
                    "Score 1.0 if the question is specific and useful, 0.0 if vague."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="no_direct_solution",
                description=(
                    "Avoids giving a full answer; prioritizes questions. "
                    "Score 1.0 if mostly questions, 0.0 if it lectures."
                ),
                weight=1.0,
                critical=True,
            ),
            JudgeCriterion(
                name="context_integration",
                description=(
                    "Builds on any provided conversation context. "
                    "Score 1.0 if context is acknowledged, 0.0 if ignored."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="question_count_control",
                description=(
                    "Uses 1-2 well-formed questions, not a barrage. "
                    "Score 1.0 if 1-2 questions, 0.5 if 3, 0.0 if more or none."
                ),
                weight=0.75,
            ),
        ]
    if reasoning == "debate":
        return [
            JudgeCriterion(
                name="debate_balance",
                description=(
                    "Presents both sides with meaningful arguments. "
                    "Score 1.0 if both sides have substance, 0.0 if one-sided."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="steelman_both_sides",
                description=(
                    "Arguments for each side are charitable and coherent. "
                    "Score 1.0 if both sides are strong, 0.0 if straw-manned."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="decision_criteria",
                description=(
                    "States criteria for choosing between sides. "
                    "Score 1.0 if criteria are explicit, 0.0 if absent."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="conclusion_after_balance",
                description=(
                    "If a conclusion is given, it comes after presenting both sides. "
                    "Score 1.0 if ordering is correct, 0.0 if premature."
                ),
                weight=0.75,
            ),
        ]
    if reasoning == "guided":
        return [
            JudgeCriterion(
                name="guided_step_progression",
                description=(
                    "Guides through a sequence of steps or decisions. "
                    "Score 1.0 if steps are explicit and ordered, 0.0 if not."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="decision_checkpoints",
                description=(
                    "Includes checkpoints or questions to assess choices. "
                    "Score 1.0 if checkpoints appear, 0.0 if missing."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="actionable_next_steps",
                description=(
                    "Suggests what to do next based on the guidance. "
                    "Score 1.0 if actionable, 0.0 if absent."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="progressive_disclosure",
                description=(
                    "Reveals complexity gradually rather than all at once. "
                    "Score 1.0 if gradual, 0.0 if dumped."
                ),
                weight=0.75,
            ),
        ]
    return []


def _style_criteria(style: str) -> list[JudgeCriterion]:
    if style == "normal":
        return [
            JudgeCriterion(
                name="style_consistency",
                description=(
                    "Maintains a neutral, professional tone throughout. "
                    "Score 1.0 if neutral and consistent, 0.0 if swings in tone."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="tone_appropriateness",
                description=(
                    "Avoids overly casual or overly formal language. "
                    "Score 1.0 if appropriate, 0.0 if off-tone."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="clarity_over_flair",
                description=(
                    "Prioritizes clarity over stylistic flourishes. "
                    "Score 1.0 if clear and straightforward, 0.0 if flowery."
                ),
                weight=0.75,
            ),
        ]
    if style == "concise":
        return [
            JudgeCriterion(
                name="conciseness_efficiency",
                description=(
                    "Conveys the essentials with minimal words. "
                    "Score 1.0 if under 80 words, 0.5 if 80-130, 0.0 if over 130."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="no_padding",
                description=(
                    "Avoids filler phrases (e.g. 'Great question', 'In conclusion'). "
                    "Score 1.0 if none, 0.0 if any."
                ),
                weight=0.75,
                must_not_contain="Great question",
            ),
            JudgeCriterion(
                name="information_density",
                description=(
                    "Each sentence adds meaning; little redundancy. "
                    "Score 1.0 if dense, 0.0 if repetitive."
                ),
                weight=0.75,
            ),
        ]
    if style == "academic":
        return [
            JudgeCriterion(
                name="academic_rigor",
                description=(
                    "Uses formal, precise, scholarly language. "
                    "Score 1.0 if consistently formal, 0.0 if casual."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="formal_register",
                description=(
                    "Avoids slang or meme language. "
                    "Score 1.0 if no slang, 0.0 if slang present."
                ),
                weight=0.75,
                must_not_contain="lol",
            ),
            JudgeCriterion(
                name="precision_of_language",
                description=(
                    "Uses precise terms and avoids ambiguous phrasing. "
                    "Score 1.0 if precise, 0.0 if vague."
                ),
                weight=0.75,
            ),
        ]
    if style == "meme":
        return [
            JudgeCriterion(
                name="informal_relatable_tone",
                description=(
                    "Uses casual, relatable phrasing to explain. "
                    "Score 1.0 if clearly informal and engaging, 0.0 if dry."
                ),
                weight=1.0,
            ),
            JudgeCriterion(
                name="humor_controlled",
                description=(
                    "Uses light humor without derailing the explanation. "
                    "Score 1.0 if playful but on-topic, 0.0 if distracting."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="still_accurate",
                description=(
                    "Informal style does not reduce correctness. "
                    "Score 1.0 if accurate, 0.0 if incorrect."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="tone_appropriateness",
                description=(
                    "Avoids offensive or insensitive jokes. "
                    "Score 1.0 if appropriate, 0.0 if offensive."
                ),
                weight=0.75,
            ),
        ]
    return []


def _capability_criteria(capabilities: frozenset[str]) -> list[JudgeCriterion]:
    criteria: list[JudgeCriterion] = []
    if "requires_search" in capabilities:
        criteria += [
            JudgeCriterion(
                name="search_integration_quality",
                description=(
                    "Incorporates the provided search context explicitly. "
                    "Score 1.0 if key terms or facts from context are used, 0.0 if ignored."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="grounding_specifics",
                description=(
                    "Uses at least two specific details from the search context. "
                    "Score 1.0 if >= 2 details, 0.5 if 1, 0.0 if none."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="attribution_clarity",
                description=(
                    "Clearly distinguishes sourced facts from general knowledge. "
                    "Score 1.0 if attribution is explicit, 0.0 if unclear."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="no_fabricated_sources",
                description=(
                    "Does not cite sources absent from the provided context. "
                    "Score 1.0 if no fabricated citations, 0.0 if any appear."
                ),
                weight=1.25,
                critical=True,
            ),
        ]
    if "requires_citations" in capabilities:
        criteria += [
            JudgeCriterion(
                name="citation_presence",
                description=(
                    "Includes explicit citations or links to sources. "
                    "Score 1.0 if at least one citation is present, 0.0 if none."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="citation_relevance",
                description=(
                    "Citations directly support claims made. "
                    "Score 1.0 if citations map to claims, 0.0 if unrelated."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="citation_accuracy",
                description=(
                    "Citations are correct and not fabricated. "
                    "Score 1.0 if accurate, 0.0 if fabricated."
                ),
                weight=1.0,
                critical=True,
            ),
            JudgeCriterion(
                name="citation_format_consistency",
                description=(
                    "Uses a consistent citation format. "
                    "Score 1.0 if consistent, 0.0 if inconsistent or missing."
                ),
                weight=0.5,
            ),
        ]
    if "requires_context" in capabilities:
        criteria += [
            JudgeCriterion(
                name="references_prior_context",
                description=(
                    "Explicitly references the provided conversation context. "
                    "Score 1.0 if referenced, 0.0 if ignored."
                ),
                weight=1.0,
                critical=True,
            ),
            JudgeCriterion(
                name="avoids_reasking_known",
                description=(
                    "Does not ask for information already provided in context. "
                    "Score 1.0 if avoids re-asking, 0.0 if re-asks."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="context_alignment",
                description=(
                    "Aligns the response with the user's stated understanding. "
                    "Score 1.0 if aligned, 0.0 if mismatched."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="context_sensitivity",
                description=(
                    "Uses the context to choose the right depth and next step. "
                    "Score 1.0 if tailored, 0.0 if generic."
                ),
                weight=0.75,
            ),
        ]
    if "requires_diagram" in capabilities:
        criteria += [
            JudgeCriterion(
                name="includes_mermaid_block",
                description=(
                    "Includes a Mermaid code block. "
                    "Score 1.0 if a Mermaid block is present, 0.0 if absent."
                ),
                weight=1.25,
                critical=True,
            ),
            JudgeCriterion(
                name="diagram_type_match",
                description=(
                    "Uses the requested diagram type. "
                    "Score 1.0 if the correct Mermaid diagram type is used, 0.0 otherwise."
                ),
                weight=1.0,
                critical=True,
            ),
            JudgeCriterion(
                name="diagram_matches_text",
                description=(
                    "Diagram matches the described mechanism. "
                    "Score 1.0 if aligned, 0.0 if unrelated."
                ),
                weight=0.75,
            ),
            JudgeCriterion(
                name="diagram_actor_coverage",
                description=(
                    "Shows at least three distinct nodes or actors. "
                    "Score 1.0 if >= 3, 0.5 if 2, 0.0 if 1 or none."
                ),
                weight=0.75,
            ),
        ]
    return criteria


def build_criteria(spec: PromptSpec) -> list[JudgeCriterion]:
    return (
        _depth_criteria(spec.depth)
        + _task_criteria(spec.task)
        + _reasoning_criteria(spec.reasoning)
        + _style_criteria(spec.style)
        + _capability_criteria(spec.capabilities)
    )


def _sanitize_text(text: str, max_len: int = 170) -> str:
    t = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    t = re.sub(r"[`{}\[\]<>|\\]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].strip()
    return t


def _topic_from_chunk(content: str, fallback: str) -> str:
    content = " ".join(str(content or "").split())
    if "**" in content:
        fragment = content.split("**", 2)[1].strip("* ")
        if 2 <= len(fragment) <= 80:
            return fragment
    if "`" in content:
        fragment = content.split("`", 2)[1].strip("` ")
        if 2 <= len(fragment) <= 80:
            return fragment
    words = [w.strip("`*(){}[],:;.") for w in content.split() if len(w.strip("`*(){}[],:;.")) > 3]
    topic = " ".join(words[:6]) or fallback
    return topic[:80].strip()


def build_supabase_benchmark_cases(size: int = 10) -> list[EvalCase]:
    from evaluation.corpus_supabase import rest_get

    params = {
        "select": "id,document_id,content,metadata,chunk_order,knowledge_documents(id,filename,source_url,metadata)",
        "limit": str(max(size * 3, 20)),
        "order": "created_at.desc",
        "content": "not.is.null",
    }
    r = rest_get("knowledge_chunks", params=params)
    r.raise_for_status()
    rows = [row for row in r.json() if len(str(row.get("content") or "")) > 120]
    if not rows:
        raise RuntimeError("No corpus chunks returned from Supabase")

    random.seed(42)
    random.shuffle(rows)
    rows = rows[:size]

    spec_templates: list[dict] = [
        {"depth": "simple", "task": "explain", "reasoning": "direct", "style": "normal", "caps": frozenset()},
        {"depth": "accessible", "task": "explain", "reasoning": "guided", "style": "concise", "caps": frozenset()},
        {"depth": "technical", "task": "compare", "reasoning": "debate", "style": "normal", "caps": frozenset()},
        {"depth": "expert", "task": "analyze", "reasoning": "direct", "style": "academic", "caps": frozenset()},
        {"depth": "simple", "task": "explain", "reasoning": "direct", "style": "meme", "caps": frozenset()},
        {"depth": "accessible", "task": "explain", "reasoning": "socratic", "style": "normal", "caps": frozenset({"requires_context"})},
        {"depth": "technical", "task": "summarize", "reasoning": "direct", "style": "normal", "caps": frozenset()},
        {"depth": "technical", "task": "brainstorm", "reasoning": "direct", "style": "normal", "caps": frozenset()},
        {"depth": "technical", "task": "explain", "reasoning": "direct", "style": "normal", "caps": frozenset({"requires_diagram"})},
        {"depth": "accessible", "task": "explain", "reasoning": "direct", "style": "normal", "caps": frozenset({"requires_search", "requires_citations"})},
    ]

    cases: list[EvalCase] = []
    for idx, row in enumerate(rows):
        doc = row.get("knowledge_documents") or {}
        content = str(row.get("content") or "")
        topic = _topic_from_chunk(content, fallback=str(doc.get("filename") or "this topic"))
        question = _sanitize_text(f"Explain {topic} in clear terms.")
        template = spec_templates[idx % len(spec_templates)]
        caps = template["caps"]
        spec = PromptSpec(
            topic=topic,
            depth=template["depth"],
            task=template["task"],
            reasoning=template["reasoning"],
            style=template["style"],
            capabilities=caps,
        )
        runtime = None
        if "requires_context" in caps:
            runtime = RuntimeContext(conversation_context=f"User said: I have seen {topic} but I'm confused about the core idea.")
            question = _sanitize_text(f"Can you help me understand {topic}?")
        if "requires_diagram" in caps:
            runtime = RuntimeContext(diagram_type=DiagramType.FLOWCHART_TD)
            question = _sanitize_text(f"Explain {topic} with a diagram.")
        if "requires_search" in caps or "requires_citations" in caps:
            source = doc.get("source_url") or doc.get("filename") or "local-corpus"
            search_context = (
                f"Source: {source}\n"
                f"Content: {content.strip()[:800]}"
            )
            runtime = RuntimeContext(search_context=search_context)
            question = _sanitize_text(f"Answer with citations: what is important about {topic}?")

        case = EvalCase(
            case_id=f"bench-supabase-{idx:02d}",
            spec=spec,
            query=question,
            runtime=runtime,
            tags=["benchmark10", "corpus"],
            criteria=build_criteria(spec),
            pass_threshold=0.7 if spec.depth != "expert" else 0.72,
            corpus_chunks=[{
                "chunk_id": row.get("id"),
                "doc_id": doc.get("id") or row.get("document_id"),
                "source": doc.get("source_url") or doc.get("filename"),
            }],
        )

        if "requires_diagram" in caps:
            case.criteria += [
                JudgeCriterion(
                    name="diagram_type_token",
                    description=(
                        "Mermaid block uses the requested flowchart TD syntax. "
                        "Score 1.0 if 'flowchart TD' appears, 0.0 if absent."
                    ),
                    weight=1.0,
                    must_contain="flowchart TD",
                    critical=True,
                )
            ]
        if "requires_citations" in caps and doc.get("source_url"):
            case.criteria += [
                JudgeCriterion(
                    name="citation_includes_source_url",
                    description=(
                        "Includes the source URL from the search context. "
                        "Score 1.0 if the URL appears, 0.0 if absent."
                    ),
                    weight=1.0,
                    must_contain=str(doc.get("source_url")),
                    critical=True,
                )
            ]
        cases.append(case)

    return cases


def load_benchmark_cases_from_file(path: str) -> list[EvalCase]:
    import json
    from pathlib import Path

    raw = Path(path).read_text(encoding="utf-8")
    items = json.loads(raw)
    cases: list[EvalCase] = []
    for item in items:
        spec_raw = item.get("prompt_spec") or {}
        caps = frozenset(spec_raw.get("capabilities") or [])
        spec = PromptSpec(
            topic=spec_raw.get("topic") or item.get("topic") or "this topic",
            depth=spec_raw.get("depth", "accessible"),
            task=spec_raw.get("task", "explain"),
            reasoning=spec_raw.get("reasoning", "direct"),
            style=spec_raw.get("style", "normal"),
            capabilities=caps,
        )
        runtime_raw = item.get("runtime") or {}
        runtime = None
        if runtime_raw:
            runtime = RuntimeContext(
                conversation_context=runtime_raw.get("conversation_context", ""),
                search_context=runtime_raw.get("search_context", ""),
                diagram_type=DiagramType(runtime_raw.get("diagram_type")) if runtime_raw.get("diagram_type") else None,
            )
        corpus_chunks = item.get("corpus_chunks") or []
        cases.append(
            EvalCase(
                case_id=item.get("case_id"),
                spec=spec,
                query=item.get("query"),
                runtime=runtime,
                tags=item.get("tags") or ["benchmark_v2"],
                criteria=build_criteria(spec),
                pass_threshold=float(item.get("pass_threshold", 0.7)),
                corpus_chunks=corpus_chunks,
                metadata={
                    "expected_behavior": item.get("expected_behavior", ""),
                    "category": item.get("category", ""),
                },
            )
        )
    return cases


# ---------------------------------------------------------------------------
# DEPTH AXIS CASES
# ---------------------------------------------------------------------------

DEPTH_CASES: list[EvalCase] = [
    EvalCase(
        case_id="depth-simple-explain",
        spec=PromptSpec(topic="TCP/IP", depth="simple", task="explain", reasoning="direct", style="normal"),
        query="What is TCP/IP?",
        tags=["depth", "simple"],
        criteria=[
            JudgeCriterion(
                name="plain_language",
                description=(
                    "The response uses simple, everyday language. "
                    "It avoids unexplained jargon. A 12-year-old could follow it. "
                    "Score 1.0 if fully plain, 0.0 if heavy technical terms appear unexplained."
                ),
            ),
            JudgeCriterion(
                name="core_idea_first",
                description=(
                    "The very first sentence or two captures the core idea. "
                    "Score 1.0 if the response leads with what TCP/IP is, 0.0 if it opens with preamble."
                ),
            ),
            JudgeCriterion(
                name="appropriate_length",
                description=(
                    "Response is brief and not padded. "
                    "Score 1.0 if under 120 words, 0.5 if 120-200, 0.0 if over 200 words."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="depth-accessible-explain",
        spec=PromptSpec(topic="B-tree indexes", depth="accessible", task="explain", reasoning="direct", style="normal"),
        query="How do B-tree indexes speed up database queries?",
        tags=["depth", "accessible"],
        criteria=[
            JudgeCriterion(
                name="defines_specialised_terms",
                description=(
                    "Specialised terms like 'node', 'leaf', or 'balanced tree' are defined "
                    "inline or via analogy the first time they appear. "
                    "Score 1.0 if every term is explained, 0.5 if partial, 0.0 if bare jargon."
                ),
            ),
            JudgeCriterion(
                name="practical_workflow_tie",
                description=(
                    "The response connects the mechanism to a practical workflow "
                    "(e.g. how a WHERE clause benefits). "
                    "Score 1.0 if concrete use-case present, 0.0 if purely theoretical."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="depth-technical-analyze",
        spec=PromptSpec(topic="Raft consensus", depth="technical", task="analyze", reasoning="direct", style="normal"),
        query="What are the failure modes of the Raft leader election mechanism?",
        tags=["depth", "technical"],
        criteria=[
            JudgeCriterion(
                name="uses_field_terminology",
                description=(
                    "The response correctly uses field-standard terms like 'term', "
                    "'election timeout', 'log replication', 'quorum'. "
                    "Score 1.0 if >= 3 are used correctly, 0.5 if 1-2, 0.0 if none."
                ),
            ),
            JudgeCriterion(
                name="failure_modes_specificity",
                description=(
                    "The response identifies at least 2 distinct failure modes "
                    "(e.g. split-brain, network partition, disk failure). "
                    "Score 1.0 if >= 2 specific modes with mechanism, 0.5 if 1, 0.0 if vague."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="depth-expert-analyze",
        spec=PromptSpec(topic="CRDTs", depth="expert", task="analyze", reasoning="direct", style="normal"),
        query="Analyze the tradeoffs between state-based and operation-based CRDTs.",
        tags=["depth", "expert"],
        criteria=[
            JudgeCriterion(
                name="tradeoffs_explicit",
                description=(
                    "The response names specific tradeoffs (e.g. bandwidth vs state size, "
                    "commutativity requirements, garbage collection complexity). "
                    "Score 1.0 if >= 3 tradeoffs with domain reasoning, 0.5 if 1-2, 0.0 if absent."
                ),
            ),
            JudgeCriterion(
                name="first_principles_framing",
                description=(
                    "The response frames concepts from first principles or formal definitions. "
                    "Score 1.0 if mathematical or formal properties are referenced, 0.0 if only high-level."
                ),
            ),
            JudgeCriterion(
                name="open_questions_or_debate",
                description=(
                    "The response acknowledges an open question, limitation, or active debate "
                    "in the research community. Score 1.0 if explicitly noted, 0.0 if absent."
                ),
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# TASK AXIS CASES
# ---------------------------------------------------------------------------

TASK_CASES: list[EvalCase] = [
    EvalCase(
        case_id="task-explain-simple",
        spec=PromptSpec(topic="DNS", depth="accessible", task="explain", reasoning="direct", style="normal"),
        query="Explain how DNS resolution works.",
        tags=["task", "explain"],
        criteria=[
            JudgeCriterion(
                name="leads_with_core_idea",
                description=(
                    "The response opens with what DNS is or does before elaborating. "
                    "Score 1.0 if the first 1-2 sentences capture the core idea, 0.0 if not."
                ),
            ),
            JudgeCriterion(
                name="no_extraneous_structure",
                description=(
                    "Explain task should NOT produce a comparison table or Option A/B structure. "
                    "Score 1.0 if prose explanation only, 0.0 if comparison structures appear."
                ),
                must_not_contain="Option A",
            ),
        ],
    ),
    EvalCase(
        case_id="task-compare-redis-postgres",
        spec=PromptSpec(topic="Redis vs PostgreSQL for session storage", depth="technical", task="compare", reasoning="direct", style="normal"),
        query="Compare Redis and PostgreSQL for storing user sessions.",
        tags=["task", "compare"],
        criteria=[
            JudgeCriterion(
                name="comparison_structure",
                description=(
                    "The response has a structured comparison: identifies both options, "
                    "lists key differences, and gives a recommendation. "
                    "Score 1.0 if all three elements present, 0.5 if two, 0.0 if one or zero."
                ),
            ),
            JudgeCriterion(
                name="actionable_tradeoffs",
                description=(
                    "Each tradeoff is specific and actionable (e.g. 'Redis loses data on restart "
                    "without AOF/RDB persistence', not just 'Redis is faster'). "
                    "Score 1.0 if >= 2 actionable tradeoffs, 0.5 if 1, 0.0 if none."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="task-brainstorm",
        spec=PromptSpec(topic="reducing LLM API costs", depth="accessible", task="brainstorm", reasoning="direct", style="normal"),
        query="Give me ideas for reducing LLM API costs in a production service.",
        tags=["task", "brainstorm"],
        criteria=[
            JudgeCriterion(
                name="idea_diversity",
                description=(
                    "The response surfaces multiple distinct ideas (ideally >= 4). "
                    "Score 1.0 if >= 4 distinct ideas, 0.5 if 2-3, 0.0 if only 1."
                ),
            ),
            JudgeCriterion(
                name="no_single_answer_structure",
                description=(
                    "Brainstorm should present options, not conclude with one recommendation. "
                    "Score 1.0 if multi-option format, 0.0 if narrows to a single answer."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="task-analyze",
        spec=PromptSpec(topic="microservices vs monolith", depth="technical", task="analyze", reasoning="direct", style="normal"),
        query="Analyze the architectural risks of migrating a monolith to microservices.",
        tags=["task", "analyze"],
        criteria=[
            JudgeCriterion(
                name="systematic_analysis",
                description=(
                    "The response systematically examines the problem rather than offering "
                    "a simple answer. Score 1.0 if multiple risk dimensions covered, 0.5 if one, 0.0 if absent."
                ),
            ),
            JudgeCriterion(
                name="identifies_risks",
                description=(
                    "At least 2 specific architectural risks are named (e.g. distributed transactions, "
                    "service discovery, data consistency). "
                    "Score 1.0 if >= 2, 0.5 if 1, 0.0 if none."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="task-summarize",
        spec=PromptSpec(topic="the CAP theorem", depth="accessible", task="summarize", reasoning="direct", style="normal"),
        query="Summarize the CAP theorem and its practical implications.",
        tags=["task", "summarize"],
        criteria=[
            JudgeCriterion(
                name="brevity",
                description=(
                    "A summarize task should be compact. "
                    "Score 1.0 if under 100 words, 0.5 if 100-180, 0.0 if over 180 words."
                ),
            ),
            JudgeCriterion(
                name="covers_all_three_letters",
                description=(
                    "The summary mentions all three components: Consistency, Availability, "
                    "and Partition Tolerance. Score 1.0 if all three mentioned, 0.5 if two, 0.0 if one."
                ),
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# REASONING AXIS CASES
# ---------------------------------------------------------------------------

REASONING_CASES: list[EvalCase] = [
    EvalCase(
        case_id="reasoning-socratic-no-context",
        spec=PromptSpec(topic="garbage collection", depth="accessible", task="explain", reasoning="socratic", style="normal"),
        query="Tell me about garbage collection.",
        tags=["reasoning", "socratic"],
        criteria=[
            JudgeCriterion(
                name="exactly_one_question",
                description=(
                    "Socratic mode with no conversation context should produce exactly one "
                    "targeted question. Score 1.0 if exactly one question mark appears in a "
                    "meaningful question, 0.5 if two, 0.0 if zero or a direct answer is given instead."
                ),
                critical=True,
            ),
            JudgeCriterion(
                name="no_direct_lecture",
                description=(
                    "The response should NOT be a lecture or explanation. "
                    "Score 1.0 if the response is a question only, 0.0 if it contains a paragraph of explanation."
                ),
                critical=True,
            ),
        ],
    ),
    EvalCase(
        case_id="reasoning-socratic-with-context",
        spec=PromptSpec(topic="garbage collection", depth="accessible", task="explain", reasoning="socratic", style="normal"),
        runtime=RuntimeContext(
            conversation_context="User said: I know GC stops the world to collect, but I don't understand why."
        ),
        query="Why does garbage collection pause the program?",
        tags=["reasoning", "socratic", "injector-context"],
        criteria=[
            JudgeCriterion(
                name="references_prior_context",
                description=(
                    "With conversation context supplied, the response should build on what "
                    "the user stated (stop-the-world awareness). "
                    "Score 1.0 if user's prior knowledge is acknowledged, 0.0 if ignored."
                ),
            ),
            JudgeCriterion(
                name="ends_with_question",
                description=(
                    "Even when giving a concise answer, Socratic mode should end with a "
                    "reflective question. Score 1.0 if response ends with a question, 0.0 if not."
                ),
                critical=True,
            ),
        ],
    ),
    EvalCase(
        case_id="reasoning-debate",
        spec=PromptSpec(topic="async vs sync API design", depth="technical", task="compare", reasoning="debate", style="normal"),
        query="Should a new public API be synchronous or asynchronous?",
        tags=["reasoning", "debate"],
        criteria=[
            JudgeCriterion(
                name="presents_both_sides",
                description=(
                    "Debate mode should present arguments for BOTH sides before concluding. "
                    "Score 1.0 if both sides have >= 1 argument each, 0.5 if one-sided with caveat, 0.0 if purely one-sided."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="reasoning-guided",
        spec=PromptSpec(topic="choosing a message queue", depth="accessible", task="explain", reasoning="guided", style="normal"),
        query="Help me choose between Kafka and RabbitMQ.",
        tags=["reasoning", "guided"],
        criteria=[
            JudgeCriterion(
                name="step_by_step_structure",
                description=(
                    "Guided reasoning should walk the user through a decision process step-by-step. "
                    "Score 1.0 if response contains numbered steps or clear sequential guidance, 0.5 if partial, 0.0 if absent."
                ),
            ),
        ],
    ),
    EvalCase(
        case_id="reasoning-direct",
        spec=PromptSpec(topic="WebSockets", depth="accessible", task="explain", reasoning="direct", style="normal"),
        query="What are WebSockets?",
        tags=["reasoning", "direct"],
        criteria=[
            JudgeCriterion(
                name="no_question_at_end",
                description=(
                    "Direct reasoning should NOT end with a reflective or Socratic question. "
                    "Score 1.0 if no question mark at the end, 0.0 if the response ends with a question."
                ),
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# STYLE AXIS CASES
# ---------------------------------------------------------------------------

STYLE_CASES: list[EvalCase] = [
    EvalCase(
        case_id="style-concise",
        spec=PromptSpec(topic="rate limiting", depth="accessible", task="explain", reasoning="direct", style="concise"),
        query="What is rate limiting and why does it matter?",
        tags=["style", "concise"],
        criteria=[
            JudgeCriterion(
                name="tight_word_count",
                description=(
                    "Concise style must be brief. "
                    "Score 1.0 if under 60 words, 0.5 if 60-100, 0.0 if over 100 words."
                ),
            ),
            JudgeCriterion(
                name="no_padding",
                description=(
                    "No filler phrases like 'Great question!', 'Certainly!', 'In conclusion'. "
                    "Score 1.0 if none present, 0.0 if any present."
                ),
                must_not_contain="Great question",
            ),
        ],
    ),
    EvalCase(
        case_id="style-academic",
        spec=PromptSpec(topic="Byzantine fault tolerance", depth="expert", task="analyze", reasoning="direct", style="academic"),
        query="Analyze Byzantine fault tolerance in distributed systems.",
        tags=["style", "academic"],
        criteria=[
            JudgeCriterion(
                name="formal_register",
                description=(
                    "The response uses formal, scholarly language without colloquialisms. "
                    "Score 1.0 if consistently formal, 0.5 if mostly formal, 0.0 if casual."
                ),
            ),
            JudgeCriterion(
                name="no_meme_language",
                description=(
                    "Academic style must not contain internet slang or meme-style humor. "
                    "Score 1.0 if none present, 0.0 if slang present."
                ),
                must_not_contain="lol",
            ),
        ],
    ),
    EvalCase(
        case_id="style-meme",
        spec=PromptSpec(topic="Docker containers", depth="simple", task="explain", reasoning="direct", style="meme"),
        query="Explain Docker containers.",
        tags=["style", "meme"],
        criteria=[
            JudgeCriterion(
                name="informal_relatable_tone",
                description=(
                    "Meme style should use casual, relatable, or humorous language to explain. "
                    "Score 1.0 if clearly informal and engaging, 0.5 if slightly casual, 0.0 if dry and academic."
                ),
            ),
            JudgeCriterion(
                name="still_accurate",
                description=(
                    "Despite informal style, the core technical fact must be correct. "
                    "Score 1.0 if accurate, 0.0 if the explanation is factually wrong due to over-simplification."
                ),
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# RUNTIME INJECTOR CASES
# ---------------------------------------------------------------------------

INJECTOR_CASES: list[EvalCase] = [
    EvalCase(
        case_id="injector-search-context",
        spec=PromptSpec(
            topic="vector similarity search",
            depth="technical",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_search"}),
        ),
        runtime=RuntimeContext(
            search_context=(
                "Source: Weaviate docs (https://weaviate.io/docs)\n"
                "Content: Weaviate uses HNSW (Hierarchical Navigable Small World) graphs "
                "for approximate nearest neighbour search, offering sub-millisecond query latency."
            )
        ),
        query="How does vector similarity search work?",
        tags=["injector", "search_context"],
        criteria=[
            JudgeCriterion(
                name="uses_retrieved_content",
                description=(
                    "The response incorporates or references the supplied search context "
                    "(e.g. mentions HNSW or Weaviate). "
                    "Score 1.0 if grounded in context, 0.5 if partially used, 0.0 if context ignored."
                ),
                must_contain="HNSW",
                critical=True,
            ),
            JudgeCriterion(
                name="no_fabricated_sources",
                description=(
                    "The response must not cite sources not present in the search context. "
                    "Score 1.0 if only grounded citations, 0.0 if hallucinated URLs or papers."
                ),
                critical=True,
            ),
        ],
    ),
    EvalCase(
        case_id="injector-diagram",
        spec=PromptSpec(
            topic="OAuth 2.0 authorization code flow",
            depth="technical",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_diagram"}),
        ),
        runtime=RuntimeContext(diagram_type=DiagramType.SEQUENCE),
        query="Explain the OAuth 2.0 authorization code flow with a diagram.",
        tags=["injector", "diagram"],
        criteria=[
            JudgeCriterion(
                name="includes_mermaid_block",
                description=(
                    "The response must include a valid Mermaid sequenceDiagram code block. "
                    "Score 1.0 if ```mermaid block with sequenceDiagram present, 0.0 if absent."
                ),
                must_contain="sequenceDiagram",
                critical=True,
            ),
            JudgeCriterion(
                name="diagram_represents_mechanism",
                description=(
                    "The diagram represents the actual OAuth flow (client, auth server, resource server), "
                    "not just a label restatement. "
                    "Score 1.0 if >= 3 distinct actors in diagram, 0.5 if 2, 0.0 if 1 or no diagram."
                ),
                critical=True,
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# INCOMPATIBILITY / EDGE-CASE CASES (expect PromptSpecError)
# ---------------------------------------------------------------------------

NEGATIVE_CASES: list[EvalCase] = [
    EvalCase(
        case_id="incompat-meme-expert",
        spec=PromptSpec(topic="Lamport clocks", depth="expert", task="explain", reasoning="direct", style="meme"),
        query="Explain Lamport clocks.",
        tags=["negative", "incompatible"],
        expect_spec_error=True,
        criteria=[],  # No LLM call; just validate error is raised
    ),
    EvalCase(
        case_id="incompat-socratic-summarize",
        spec=PromptSpec(topic="Kubernetes", depth="accessible", task="summarize", reasoning="socratic", style="normal"),
        query="Summarize Kubernetes.",
        tags=["negative", "incompatible"],
        expect_spec_error=True,
        criteria=[],
    ),
    EvalCase(
        case_id="missing-search-context",
        spec=PromptSpec(
            topic="vector databases",
            depth="technical",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_search"}),
        ),
        query="Explain vector databases using the provided sources.",
        tags=["negative", "missing-runtime"],
        expect_spec_error=True,
        criteria=[],
    ),
    EvalCase(
        case_id="missing-diagram-context",
        spec=PromptSpec(
            topic="JWT verification",
            depth="technical",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_diagram"}),
        ),
        query="Explain JWT verification with a diagram.",
        tags=["negative", "missing-runtime"],
        expect_spec_error=True,
        criteria=[],
    ),
    EvalCase(
        case_id="unknown-capability",
        spec=PromptSpec(
            topic="caching",
            depth="accessible",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_time_travel"}),
        ),
        query="Explain caching.",
        tags=["negative", "invalid-capability"],
        expect_spec_error=True,
        criteria=[],
    ),
]


# ---------------------------------------------------------------------------
# RICH 10-CASE BENCHMARK (uses corpus queries from evaluation/queries.json)
# ---------------------------------------------------------------------------

_STATIC_BENCHMARK_CASES: list[EvalCase] = [
    EvalCase(
        case_id="bench-simple-direct-normal-explain",
        spec=PromptSpec(topic="Big O notation", depth="simple", task="explain", reasoning="direct", style="normal"),
        query="What is Big O notation and time complexity analysis?",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="Big O notation", depth="simple", task="explain", reasoning="direct", style="normal")
        ),
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-accessible-guided-concise-explain",
        spec=PromptSpec(topic="backpropagation", depth="accessible", task="explain", reasoning="guided", style="concise"),
        query="How does backpropagation work in neural networks?",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="backpropagation", depth="accessible", task="explain", reasoning="guided", style="concise")
        ),
        pass_threshold=0.72,
    ),
    EvalCase(
        case_id="bench-technical-debate-normal-compare",
        spec=PromptSpec(topic="BFS vs DFS", depth="technical", task="compare", reasoning="debate", style="normal"),
        query="What is the difference between BFS and DFS graph traversal?",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="BFS vs DFS", depth="technical", task="compare", reasoning="debate", style="normal")
        ),
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-expert-direct-academic-analyze",
        spec=PromptSpec(topic="CAP theorem", depth="expert", task="analyze", reasoning="direct", style="academic"),
        query="Explain the CAP theorem in distributed systems",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="CAP theorem", depth="expert", task="analyze", reasoning="direct", style="academic")
        ),
        pass_threshold=0.72,
    ),
    EvalCase(
        case_id="bench-simple-direct-meme-explain",
        spec=PromptSpec(topic="list comprehensions", depth="simple", task="explain", reasoning="direct", style="meme"),
        query="Python list comprehension with filter condition",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="list comprehensions", depth="simple", task="explain", reasoning="direct", style="meme")
        ),
        pass_threshold=0.68,
    ),
    EvalCase(
        case_id="bench-accessible-socratic-context-explain",
        spec=PromptSpec(
            topic="Python GIL",
            depth="accessible",
            task="explain",
            reasoning="socratic",
            style="normal",
            capabilities=frozenset({"requires_context"}),
        ),
        runtime=RuntimeContext(
            conversation_context="User knows the GIL exists but is confused why CPU-bound threads do not speed up."
        ),
        query="How does the GIL affect Python multithreading?",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(
                topic="Python GIL",
                depth="accessible",
                task="explain",
                reasoning="socratic",
                style="normal",
                capabilities=frozenset({"requires_context"}),
            )
        ),
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-technical-direct-normal-summarize",
        spec=PromptSpec(topic="asyncio await", depth="technical", task="summarize", reasoning="direct", style="normal"),
        query="async def and await syntax in Python asyncio",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="asyncio await", depth="technical", task="summarize", reasoning="direct", style="normal")
        ),
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-technical-direct-normal-brainstorm",
        spec=PromptSpec(topic="LRU cache", depth="technical", task="brainstorm", reasoning="direct", style="normal"),
        query="Implementing a LRU cache with O(1) lookup",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(topic="LRU cache", depth="technical", task="brainstorm", reasoning="direct", style="normal")
        ),
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-technical-direct-normal-diagram",
        spec=PromptSpec(
            topic="binary search tree",
            depth="technical",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_diagram"}),
        ),
        runtime=RuntimeContext(diagram_type=DiagramType.FLOWCHART_TD),
        query="How to implement a binary search tree in Python?",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(
                topic="binary search tree",
                depth="technical",
                task="explain",
                reasoning="direct",
                style="normal",
                capabilities=frozenset({"requires_diagram"}),
            )
        )
        + [
            JudgeCriterion(
                name="diagram_type_token",
                description=(
                    "Mermaid block uses the requested flowchart TD syntax. "
                    "Score 1.0 if 'flowchart TD' appears, 0.0 if absent."
                ),
                weight=1.0,
                must_contain="flowchart TD",
                critical=True,
            )
        ],
        pass_threshold=0.7,
    ),
    EvalCase(
        case_id="bench-accessible-direct-normal-citations",
        spec=PromptSpec(
            topic="SQL JOIN types",
            depth="accessible",
            task="explain",
            reasoning="direct",
            style="normal",
            capabilities=frozenset({"requires_search", "requires_citations"}),
        ),
        runtime=RuntimeContext(
            search_context=(
                "Source: PostgreSQL docs (https://www.postgresql.org/docs/current/queries-table-expressions.html)\n"
                "Content: INNER JOIN returns only matching rows. LEFT JOIN returns all rows from the left table with NULLs on no match. "
                "RIGHT JOIN returns all rows from the right table. FULL OUTER JOIN returns all rows from both tables.\n"
                "Source: SQLite docs (https://sqlite.org/lang_select.html)\n"
                "Content: The JOIN clause combines rows from two tables based on a join condition."
            )
        ),
        query="SQL JOIN types: INNER, LEFT, RIGHT, FULL OUTER",
        tags=["benchmark10"],
        criteria=build_criteria(
            PromptSpec(
                topic="SQL JOIN types",
                depth="accessible",
                task="explain",
                reasoning="direct",
                style="normal",
                capabilities=frozenset({"requires_search", "requires_citations"}),
            )
        )
        + [
            JudgeCriterion(
                name="citation_includes_pg_url",
                description=(
                    "Includes the PostgreSQL docs URL from the search context. "
                    "Score 1.0 if the URL appears, 0.0 if absent."
                ),
                weight=1.0,
                must_contain="https://www.postgresql.org/docs/current/queries-table-expressions.html",
                critical=True,
            )
        ],
        pass_threshold=0.72,
    ),
]


def _load_benchmark_cases() -> list[EvalCase]:
    return _STATIC_BENCHMARK_CASES


BENCHMARK_CASES: list[EvalCase] = _load_benchmark_cases()


# ---------------------------------------------------------------------------
# FULL SUITE
# ---------------------------------------------------------------------------

ALL_CASES: list[EvalCase] = (
    DEPTH_CASES
    + TASK_CASES
    + REASONING_CASES
    + STYLE_CASES
    + INJECTOR_CASES
    + NEGATIVE_CASES
    + BENCHMARK_CASES
)
