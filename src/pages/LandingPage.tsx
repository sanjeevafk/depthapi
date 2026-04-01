import { motion } from "framer-motion";
import { Link, useNavigate } from "react-router-dom";
import { useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  Search,
  Layers,
  CheckCircle2,
  Brain,
  Zap,
  BookOpen,
  Terminal,
  MessageSquare,
} from "lucide-react";
import { LoginButton } from "../components/LoginButton";
import { LivePreviewCard } from "../components/LivePreviewCard";
import { useAuth } from "../context/AuthContext";
import { useEffect, type ReactNode } from "react";
import { Helmet } from "react-helmet-async";
import {
  buildTitle,
  getBaseUrl,
  getOgImageUrl,
  getSiteName,
} from "../lib/seo";

export default function LandingPage(): JSX.Element {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const stayOnLanding = searchParams.get("stay") === "1";
    if (user && !stayOnLanding) {
      navigate("/app");
    }
  }, [user, navigate, searchParams]);

  return (
    <div className="min-h-screen bg-black text-white selection:bg-cyan-500/30">
      <Helmet>
        <title>
          {buildTitle(
            "AI Learning Tool — Multi-Model Socratic Workspace",
          )}
        </title>
        <meta
          name="description"
          content="KnowBear is a multi-model AI learning assistant. Use Learn, Socratic, and Technical modes to understand deep concepts through layered, context-aware conversations."
        />
        <meta
          name="keywords"
          content="AI learning tool, developer assistant, multi-model AI, Socratic learning, depth control AI, educational workspace"
        />
        <meta
          property="og:title"
          content={`${getSiteName()} — Multi-Model AI Learning Workspace`}
        />
        <meta
          property="og:description"
          content="Understand deep concepts through Learn, Socratic, and Technical modes with multi-model routing."
        />
        <meta property="og:image" content={getOgImageUrl()} />
        <meta property="og:url" content={`${getBaseUrl()}/`} />
        <meta property="og:type" content="website" />
        <script type="application/ld+json">
          {JSON.stringify({
            "@context": "https://schema.org",
            "@type": "Organization",
            name: getSiteName(),
            url: getBaseUrl(),
            logo: `${getBaseUrl()}/favicon.svg`,
          })}
        </script>
        <script type="application/ld+json">
          {JSON.stringify({
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            name: getSiteName(),
            applicationCategory: "EducationalApplication",
            operatingSystem: "Web",
            description:
              "AI learning workspace delivering layered explanations and Socratic reasoning.",
            offers: {
              "@type": "Offer",
              price: "0",
              priceCurrency: "USD",
            },
            url: getBaseUrl(),
          })}
        </script>
      </Helmet>
      {/* Starry Background */}
      <div className="fixed inset-0 z-0">
        <div className="stars"></div>
        <div className="stars stars-2"></div>
        <div className="absolute inset-0 bg-gradient-to-b from-cyan-950/20 via-black to-black"></div>
      </div>

      {/* Navigation */}
      <nav className="relative z-50 flex flex-wrap items-center gap-3 px-4 py-4 sm:flex-nowrap sm:justify-between sm:px-6 sm:py-6 max-w-7xl mx-auto">
        <div
          className="flex min-w-0 items-center gap-2 group cursor-pointer"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        >
          <img
            src="/favicon.svg"
            alt="Logo"
            className="w-9 h-9 sm:w-10 sm:h-10 drop-shadow-[0_0_8px_rgba(6,182,212,0.8)]"
          />
          <div className="flex flex-col min-w-0">
            <span className="text-lg sm:text-2xl font-black tracking-tighter leading-none whitespace-nowrap">
              Know<span className="text-cyan-500">Bear</span>
            </span>
          </div>
        </div>
        {/* Login button — visible on all screen sizes */}
        <div className="ml-auto flex items-center shrink-0">
          <LoginButton className="!px-3.5 !py-2 !text-xs md:!px-6 md:!py-2.5 md:!text-sm font-bold bg-white text-black hover:bg-gray-200 border-none rounded-full shadow-[0_0_20px_rgba(255,255,255,0.4)] hover:shadow-[0_0_25px_rgba(255,255,255,0.6)] transition-all transform hover:scale-105" />
        </div>
      </nav>

      <main className="relative z-10">
        {/* Hero Section */}
        <section className="pt-12 pb-20 md:pt-20 md:pb-32 px-4 sm:px-6 overflow-hidden">
          <div className="max-w-5xl mx-auto text-center">
            <motion.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 1, type: "spring" }}
              className="inline-flex flex-wrap justify-center sm:flex-nowrap items-center gap-x-2 gap-y-1 p-2 px-3 sm:p-3 sm:px-6 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-[0.65rem] sm:text-xs font-bold uppercase tracking-widest sm:tracking-[0.2em] mb-6 sm:mb-8 max-w-[95vw] text-center"
            >
              Multi-Model Socratic Workspace
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
              className="text-4xl sm:text-6xl md:text-8xl lg:text-9xl font-black tracking-tighter leading-[0.9] mb-6 sm:mb-8"
            >
              Master Any Topic. <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 via-blue-500 to-indigo-600">
                Driven by Proof.
              </span>
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.5 }}
              className="text-gray-400 text-base sm:text-lg md:text-xl max-w-3xl mx-auto mb-10 sm:mb-12 leading-relaxed"
            >
              KnowBear intelligently routes queries through Learn, Socratic, and Technical workflows, utilizing cross-model MoE to deliver context-aware, verifiable explanations.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.7 }}
              className="flex flex-col items-center justify-center gap-4"
            >
              <button
                onClick={() => navigate("/app")}
                className="w-full sm:w-auto px-8 sm:px-10 py-4 sm:py-5 bg-cyan-600 hover:bg-cyan-500 text-white rounded-full font-black text-base sm:text-lg shadow-[0_20px_50px_rgba(8,145,178,0.3)] transition-all hover:scale-105 active:scale-95 flex items-center justify-center gap-3 group"
              >
                Open Workspace
                <ArrowRight className="w-5 h-5 sm:w-6 sm:h-6 group-hover:translate-x-1 transition-transform" />
              </button>
              <p className="mt-4 sm:mt-8 text-cyan-500/50 text-sm font-medium tracking-wide italic max-w-sm mx-auto">
                "An explanation engine that adapts depth, tone, and structure
                to how you learn."
              </p>
            </motion.div>
          </div>
        </section>

        {/* Core Features Grid */}
        <section
          id="features"
          className="py-16 sm:py-28 px-4 sm:px-6 bg-white/[0.02] border-y border-white/5"
        >
          <div className="max-w-7xl mx-auto">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 sm:gap-8">
              <FeatureCard
                icon={<Brain className="w-7 h-7 sm:w-8 sm:h-8 text-cyan-400" />}
                title="Multi-Model MoE"
                description="Routes every query to the optimal model based on complexity and mode."
              />
              <FeatureCard
                icon={<Layers className="w-7 h-7 sm:w-8 sm:h-8 text-purple-400" />}
                title="Mode-Based Workflows"
                description="Toggle between Learn, Socratic, and Technical spaces for targeted knowledge retrieval."
              />
              <FeatureCard
                icon={<Search className="w-7 h-7 sm:w-8 sm:h-8 text-blue-400" />}
                title="Live Search Context"
                description="Injects real-time web data into conversations to ensure accuracy and freshness."
              />
              <FeatureCard
                icon={<Zap className="w-7 h-7 sm:w-8 sm:h-8 text-amber-400" />}
                title="Stream-Native UX"
                description="Ultra-fast response streaming with redundant provider fallback for zero downtime."
              />
            </div>
          </div>
        </section>

        {/* Specialized Modes Section */}
        <section id="modes" className="py-20 sm:py-32 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto">
            <div className="text-center mb-16 sm:mb-20">
              <h2 className="text-3xl sm:text-5xl font-black mb-4 sm:mb-6">
                Three Modes. <span className="text-cyan-500">One Workspace.</span>
              </h2>
              <p className="text-gray-400 max-w-2xl mx-auto">
                Choose the workflow that fits your intent. Each mode uses
                specialized prompting and model parameters.
              </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <ModeDisplay
                icon={<BookOpen className="w-8 h-8 text-cyan-400" />}
                mode="Learn"
                description="Concise explanations with adaptive depth control (ELI5 to Expert)."
              />
              <ModeDisplay
                icon={<MessageSquare className="w-8 h-8 text-purple-400" />}
                mode="Socratic"
                description="Guided reasoning and assumption testing through question-driven dialogue."
              />
              <ModeDisplay
                icon={<Terminal className="w-8 h-8 text-blue-400" />}
                mode="Technical"
                description="Implementation-focused insights with structured diagrams and first-principles logic."
              />
            </div>
          </div>
        </section>

        {/* Intelligence Section */}
        <section id="models" className="py-20 sm:py-32 px-4 sm:px-6 bg-black/50">
          <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-20 items-center">
            <div>
              <h2 className="text-3xl sm:text-4xl md:text-6xl font-black tracking-tight mb-8 sm:mb-12">
                Built for <span className="text-cyan-500">Reliable</span>{" "}
                Knowledge, Not Just Chat.
              </h2>
              <div className="space-y-8">
                <CheckItem
                  title="Context-Aware Conversations"
                  description="Maintain deep thread lineage across session reloads with dedicated workspace isolation."
                />
                <CheckItem
                  title="Fine-Grained Depth Tuning"
                  description="Toggle from ELI5 to Expert deep-dives instantly to bridge understanding gaps without losing context."
                />
                <CheckItem
                  title="Resilient Infrastructure"
                  description="Automatic routing through redundant model providers (Claude, GPT, Gemini) for unmatched availability."
                />
              </div>
            </div>
            <div className="relative group perspective-1000">
              <div className="glow-layer absolute -inset-4 bg-gradient-to-r from-cyan-500 to-blue-600 rounded-3xl blur-2xl opacity-20 group-hover:opacity-40 transition-opacity"></div>
              <div className="relative">
                <LivePreviewCard />
              </div>
            </div>
          </div>
        </section>

        {/* Tools & Export */}
        <section
          id="export"
          className="py-16 sm:py-24 px-4 sm:px-6 border-t border-white/5 bg-white/[0.01]"
        >
          <div className="max-w-5xl mx-auto text-center">
            <h2 className="text-3xl sm:text-5xl font-black mb-6 sm:mb-8">
              Take your insights anywhere.
            </h2>
            <p className="text-gray-400 max-w-2xl mx-auto text-sm sm:text-lg mb-12 leading-relaxed">
              Export high-quality explanations as Markdown, download full chat histories, or sync datasets for your own training. Professional tools for professional learning.
            </p>
          </div>
        </section>

        {/* Footer */}
        <footer className="py-20 sm:py-28 px-4 sm:px-6 border-t border-white/5 bg-black">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center gap-12 md:gap-16">
            <div className="flex flex-col items-center md:items-start gap-4 text-center md:text-left">
              <div className="flex items-center gap-3">
                <img
                  src="/favicon.svg"
                  alt="Knowledge Learning Engine Logo"
                  className="w-10 h-10 opacity-70 grayscale hover:grayscale-0 transition-all"
                />
                <span className="text-2xl font-black tracking-tighter opacity-70">
                  Know<span className="text-white">Bear</span>
                </span>
              </div>
              <p className="text-gray-600 text-sm max-w-xs leading-relaxed">
                Multi-model AI workspace for verifiable learning and Socratic reasoning.
              </p>
            </div>
            <div className="flex flex-wrap justify-center items-center gap-8 text-gray-400 text-sm font-bold">
              <Link
                to="/features"
                className="hover:text-cyan-400 transition-colors uppercase tracking-widest"
              >
                Features
              </Link>
              <Link
                to="/pricing"
                className="hover:text-cyan-400 transition-colors uppercase tracking-widest"
              >
                Pricing
              </Link>
              <Link
                to="/terms"
                className="hover:text-cyan-400 transition-colors uppercase tracking-widest"
              >
                Terms
              </Link>
              <Link
                to="/privacy"
                className="hover:text-cyan-400 transition-colors uppercase tracking-widest"
              >
                Privacy
              </Link>
              <span className="text-gray-700 select-none">© 2026 KnowBear</span>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}): JSX.Element {
  return (
    <motion.div
      whileHover={{ y: -5 }}
      className="p-5 sm:p-8 bg-dark-800/50 border border-white/5 rounded-3xl hover:border-white/10 transition-all flex flex-col items-start gap-5 sm:gap-6"
    >
      <div className="p-3 sm:p-4 bg-white/5 rounded-2xl">{icon}</div>
      <div>
        <h3 className="text-lg sm:text-xl font-bold mb-2 sm:mb-3">{title}</h3>
        <p className="text-gray-500 text-sm leading-relaxed">{description}</p>
      </div>
    </motion.div>
  );
}

function ModeDisplay({
  icon,
  mode,
  description,
}: {
  icon: ReactNode;
  mode: string;
  description: string;
}): JSX.Element {
  return (
    <div className="p-8 sm:p-10 bg-white/[0.03] border border-white/5 rounded-3xl text-center flex flex-col items-center gap-6">
      <div className="p-4 bg-white/5 rounded-full mb-2">{icon}</div>
      <div>
        <h3 className="text-2xl font-black mb-4">{mode}</h3>
        <p className="text-gray-400 text-sm leading-relaxed">{description}</p>
      </div>
    </div>
  );
}

function CheckItem({
  title,
  description,
}: {
  title: string;
  description: string;
}): JSX.Element {
  return (
    <div className="flex gap-4">
      <CheckCircle2 className="w-6 h-6 text-cyan-500 shrink-0" />
      <div>
        <h4 className="font-bold text-white mb-1">{title}</h4>
        <p className="text-gray-500 text-sm">{description}</p>
      </div>
    </div>
  );
}
