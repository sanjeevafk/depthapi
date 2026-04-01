import type { Mode } from "../types";
import type { ConversationMode } from "../types/chat";

type StreamingMode = ConversationMode | Mode;

const LEARN_VERBS = [
  "Explaining",
  "Simplifying",
  "Teaching",
  "Guiding",
  "Building intuition",
  "Making sense",
  "Clarifying",
  "Breaking down",
  "Connecting dots",
  "Unfolding concepts",
  "Nurturing understanding",
  "Demystifying",
  "Walking through",
  "Layering knowledge",
  "Making intuitive",
  "Growing comprehension",
] as const;

const SOCRATIC_VERBS = [
  "Questioning",
  "Probing",
  "Challenging assumptions",
  "Exploring ideas",
  "Dialoguing",
  "Socratically inquiring",
  "Unearthing assumptions",
  "Stimulating thought",
  "Reflecting deeply",
  "Examining perspectives",
  "Uncovering truths",
  "Facilitating inquiry",
  "Provoking reflection",
  "Doubting wisely",
  "Seeking clarity",
  "Testing understanding",
  "Engaging curiosity",
  "Dissecting beliefs",
  "Encouraging wonder",
  "Thinking together",
] as const;

const TECHNICAL_VERBS = [
  "Analyzing",
  "Dissecting",
  "Deconstructing",
  "Synthesizing",
  "Refining",
  "Architecting",
  "Mapping mechanisms",
  "Deriving rigorously",
  "Formalizing",
  "Crystallizing details",
  "Structuring precisely",
  "Deep diving",
  "Modeling accurately",
  "Engineering understanding",
] as const;

const STREAMING_VERBS: Partial<Record<StreamingMode, readonly string[]>> = {
  learn: LEARN_VERBS,
  socratic: SOCRATIC_VERBS,
  technical: TECHNICAL_VERBS,
  eli5: LEARN_VERBS,
  eli10: LEARN_VERBS,
  eli12: LEARN_VERBS,
  eli15: LEARN_VERBS,
  meme: LEARN_VERBS,
};

export function getStreamingVerbs(
  mode?: StreamingMode,
): readonly string[] | null {
  if (!mode) return null;
  return STREAMING_VERBS[mode] ?? null;
}
