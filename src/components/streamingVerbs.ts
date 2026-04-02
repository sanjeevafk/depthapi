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

const MEME_VERBS = [
  "Meming",
  "Shitposting",
  "Vibing",
  "Roasting concepts",
  "Cooking",
  "Yeeting facts",
  "Dabbing on ignorance",
  "Trolling the textbooks",
  "Loading epicness",
  "Dropping knowledge bombs",
  "Being based",
  "Spilling the tea",
  "Flexing wisdom",
  "Avoiding cringe",
  "Expanding the brain",
  "Big brain time",
  "Drake approving facts",
  "Expanding brain",
  "Surprised Pikachu",
  "Epic W loading",
  "Loading troll face",
] as const;

const STREAMING_VERBS: Partial<Record<StreamingMode, readonly string[]>> = {
  learn: LEARN_VERBS,
  socratic: SOCRATIC_VERBS,
  technical: TECHNICAL_VERBS,
  eli5: LEARN_VERBS,
  eli10: LEARN_VERBS,
  eli12: LEARN_VERBS,
  eli15: LEARN_VERBS,
  meme: MEME_VERBS,
};

function resolveStreamingMode(
  mode?: StreamingMode,
  promptMode?: StreamingMode,
): StreamingMode | undefined {
  if (promptMode && STREAMING_VERBS[promptMode]) {
    return promptMode;
  }
  return mode;
}

export function getStreamingVerbs(
  mode?: StreamingMode,
  promptMode?: StreamingMode,
): readonly string[] | null {
  const resolved = resolveStreamingMode(mode, promptMode);
  if (!resolved) return null;
  return STREAMING_VERBS[resolved] ?? null;
}
