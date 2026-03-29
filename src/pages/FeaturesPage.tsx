import { Helmet } from "react-helmet-async";
import LegalPageLayout from "../components/LegalPageLayout";

export default function FeaturesPage(): JSX.Element {
  return (
    <LegalPageLayout title="Features" lastUpdated="March 30, 2026">
      <Helmet>
        <title>KnowBear Features | Layered AI Learning Platform</title>
        <meta
          name="description"
          content="Explore KnowBear features: layered explanations, learning modes, exportable notes, and reliable AI tutoring for students and professionals."
        />
        <meta property="og:title" content="KnowBear Features" />
      </Helmet>

      <section className="space-y-4">
        <p>
          KnowBear is built for teaching-first AI experiences. These features
          help you learn faster, retain more, and share explanations with
          confidence.
        </p>
      </section>

      <section className="space-y-6">
        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-dark-800">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Layered Explanations
          </h2>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            Switch between ELI5 and technical depth without rewriting your
            prompt. Keep context and adjust complexity instantly.
          </p>
        </div>

        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-dark-800">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Learning Modes
          </h2>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            Use Learn, Socratic, or Technical modes to match your study style and
            get the right kind of guidance.
          </p>
        </div>

        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-dark-800">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Exportable Notes
          </h2>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            Export responses to clean markdown or text for study guides, docs,
            and team collaboration.
          </p>
        </div>
      </section>
    </LegalPageLayout>
  );
}
