import { Helmet } from "react-helmet-async";
import LegalPageLayout from "../components/LegalPageLayout";

export default function PricingPage(): JSX.Element {
  return (
    <LegalPageLayout title="Pricing" lastUpdated="March 30, 2026">
      <Helmet>
        <title>KnowBear Pricing | AI Learning Workspace Plans</title>
        <meta
          name="description"
          content="Compare KnowBear pricing plans for our AI learning workspace. Choose the right plan for layered explanations, study workflows, and team learning."
        />
        <meta property="og:title" content="KnowBear Pricing" />
      </Helmet>

      <section className="space-y-4">
        <p>
          Choose the plan that fits your learning workflow. Upgrade any time and
          cancel when you no longer need premium access.
        </p>
      </section>

      <section className="grid gap-6 sm:grid-cols-2">
        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-dark-800">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Starter
          </h2>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            Ideal for trying KnowBear and light daily usage.
          </p>
          <p className="mt-6 text-3xl font-semibold text-slate-900 dark:text-white">
            $0
          </p>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Basic access
          </p>
          <ul className="mt-4 list-disc space-y-2 pl-5">
            <li>Limited daily explanations</li>
            <li>Standard response speed</li>
            <li>Email support</li>
          </ul>
        </div>

        <div className="rounded-3xl border border-cyan-500/40 bg-white p-6 shadow-sm dark:border-cyan-400/30 dark:bg-dark-800">
          <div className="inline-flex rounded-full bg-cyan-500/10 px-3 py-1 text-xs font-semibold text-cyan-500">
            Most popular
          </div>
          <h2 className="mt-3 text-lg font-semibold text-slate-900 dark:text-white">
            Pro
          </h2>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            For focused learning, heavy usage, and premium modes.
          </p>
          <p className="mt-6 text-3xl font-semibold text-slate-900 dark:text-white">
            $5
          </p>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            per month
          </p>
          <ul className="mt-4 list-disc space-y-2 pl-5">
            <li>Unlimited explanations</li>
            <li>Premium modes and depth controls</li>
            <li>Priority support</li>
          </ul>
        </div>
      </section>
    </LegalPageLayout>
  );
}
