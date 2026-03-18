import { BrainCircuit, HelpCircle, Sparkles } from "lucide-react";
import type { Workspace } from "../../stores/useChatStore";

type PromptTile = {
  id: string;
  title: string;
  description: string;
  prompt: string;
};

export const WORKSPACE_CONTENT: Record<Workspace, { title: string; description: string }> = {
  learn: {
    title: "Welcome to Learn Mode",
    description: "Pick a depth level and ask anything you want to understand.",
  },
  socratic: {
    title: "Welcome to Socratic Mode",
    description: "Start with a question and we will reason through it together.",
  },
  technical: {
    title: "Welcome to Technical Mode",
    description: "Share a system, bug, or architecture topic for a deeper breakdown.",
  },
};

export const WORKSPACE_ICONS = {
  learn: Sparkles,
  socratic: HelpCircle,
  technical: BrainCircuit,
};

export const WORKSPACE_PROMPTS: Record<Workspace, PromptTile[]> = {
  learn: [
    {
      id: "learn-1",
      title: "Explain a core concept",
      description: "Break down photosynthesis in simple terms.",
      prompt: "Explain photosynthesis like I'm new to biology.",
    },
    {
      id: "learn-2",
      title: "Teach me with examples",
      description: "Understand supply and demand through a real story.",
      prompt: "Explain supply and demand with a real-world example.",
    },
    {
      id: "learn-3",
      title: "Compare two ideas",
      description: "Spot the difference between RAM and storage.",
      prompt: "What's the difference between RAM and storage?",
    },
    {
      id: "learn-4",
      title: "Summarize a topic",
      description: "Get a crisp overview of the French Revolution.",
      prompt: "Summarize the French Revolution in five bullet points.",
    },
    {
      id: "learn-5",
      title: "Start a quick lesson",
      description: "Understand how neural networks learn.",
      prompt: "Explain how a neural network learns, step by step.",
    },
  ],
  socratic: [
    {
      id: "socratic-1",
      title: "Question my assumptions",
      description: "Explore whether remote work improves productivity.",
      prompt: "Help me reason about whether remote work improves productivity.",
    },
    {
      id: "socratic-2",
      title: "Weigh trade-offs",
      description: "Balance privacy and security concerns.",
      prompt: "Guide me through the trade-offs between privacy and security.",
    },
    {
      id: "socratic-3",
      title: "Make a tough choice",
      description: "Think through choosing a career path.",
      prompt: "Ask me questions to help choose a career path.",
    },
    {
      id: "socratic-4",
      title: "Explore a complex topic",
      description: "Probe the pros and cons of nuclear energy.",
      prompt: "Use questions to explore the pros and cons of nuclear energy.",
    },
    {
      id: "socratic-5",
      title: "Challenge a belief",
      description: "Examine AI replacing jobs.",
      prompt: "Question my assumptions about AI replacing jobs.",
    },
  ],
  technical: [
    {
      id: "technical-1",
      title: "Design a system",
      description: "Build a scalable Redis rate limiter.",
      prompt: "Design a scalable Redis rate limiter for an API.",
    },
    {
      id: "technical-2",
      title: "Debug a frontend issue",
      description: "Find why a React component keeps re-rendering.",
      prompt: "Help me debug why a React component re-renders on every keystroke.",
    },
    {
      id: "technical-3",
      title: "Model data efficiently",
      description: "Pick an index strategy for time-series data.",
      prompt: "Explain an index strategy for time-series data.",
    },
    {
      id: "technical-4",
      title: "Review architecture",
      description: "Plan a multi-tenant SaaS setup.",
      prompt: "Review an architecture for a multi-tenant SaaS application.",
    },
    {
      id: "technical-5",
      title: "Implement OAuth",
      description: "Understand PKCE and secure login flows.",
      prompt: "Explain how to implement OAuth PKCE securely.",
    },
  ],
};

export type { PromptTile };
