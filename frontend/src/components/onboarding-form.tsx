"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { api } from "@/lib/api";
import type { CreatorPreferencesUpdate } from "@/lib/types";

const GOALS = [
  "Grow reach",
  "Increase saves",
  "Drive engagement",
  "Build authority",
  "Generate leads",
];

const INITIAL: CreatorPreferencesUpdate = {
  target_countries: [],
  target_cities: [],
  content_languages: [],
  timezone: "",
  niches: [],
  goals: [],
  constraints: [],
};

export function OnboardingForm() {
  const router = useRouter();
  const { refreshUser } = useAuth();
  const [values, setValues] = useState(INITIAL);
  const [constraints, setConstraints] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const language = navigator.language;
    api
      .getPreferences()
      .then((preferences) => {
        if (!active) return;
        setValues({
          target_countries: preferences.target_countries ?? [],
          target_cities: preferences.target_cities ?? [],
          content_languages: preferences.content_languages?.length
            ? preferences.content_languages
            : [language],
          timezone: preferences.timezone || timezone,
          niches: preferences.niches ?? [],
          goals: preferences.goals ?? [],
          constraints: preferences.constraints ?? [],
        });
        setConstraints((preferences.constraints ?? []).join("\n"));
      })
      .catch(() => {
        if (active) {
          setValues((current) => ({
            ...current,
            content_languages: [language],
            timezone,
          }));
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  function update<K extends keyof CreatorPreferencesUpdate>(
    key: K,
    value: CreatorPreferencesUpdate[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setFieldErrors((current) => ({ ...current, [key]: "" }));
  }

  function toggleGoal(goal: string) {
    update(
      "goals",
      values.goals.includes(goal)
        ? values.goals.filter((item) => item !== goal)
        : [...values.goals, goal],
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    if (!values.target_countries.length)
      nextErrors.target_countries = "Choose a target market.";
    if (!values.content_languages[0]?.trim())
      nextErrors.content_languages = "Enter a content language.";
    if (!values.timezone.trim()) nextErrors.timezone = "Enter a timezone.";
    if (!values.niches[0]?.trim()) nextErrors.niches = "Describe your niche.";
    if (!values.goals.length) nextErrors.goals = "Choose at least one goal.";
    if (Object.keys(nextErrors).length) {
      setFieldErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      await api.updatePreferences({
        ...values,
        content_languages: values.content_languages.map((item) => item.trim()).filter(Boolean),
        target_cities: values.target_cities.map((item) => item.trim()).filter(Boolean),
        timezone: values.timezone.trim(),
        niches: values.niches.map((item) => item.trim()).filter(Boolean),
        constraints: constraints
          .split(/\n|,/)
          .map((item) => item.trim())
          .filter(Boolean),
      });
      await refreshUser();
      router.replace("/dashboard");
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save preferences.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="page-center" role="status">
        <span className="spinner" aria-hidden="true" />
        Loading your preferences...
      </div>
    );
  }

  return (
    <main className="page-container max-w-3xl!">
      <header className="mb-8">
        <p className="eyebrow">Creator setup</p>
        <h1 className="page-title mt-2">Make every brief feel like yours</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
          These choices guide which market signals we use and how every idea is written.
        </p>
      </header>
      <form className="card p-5 sm:p-8" onSubmit={submit} noValidate>
        {error && <div className="alert alert-error mb-6" role="alert">{error}</div>}
        <fieldset className="grid gap-5 sm:grid-cols-2">
          <legend className="section-title col-span-full">Audience and voice</legend>
          <SelectField
            id="target-market"
            label="Target market"
            value={values.target_countries[0] ?? ""}
            error={fieldErrors.target_countries}
            onChange={(value) => update("target_countries", value ? [value] : [])}
            options={[
              ["", "Select a market"],
              ["TR", "Türkiye"],
              ["US", "United States"],
              ["GB", "United Kingdom"],
              ["DE", "Germany"],
              ["FR", "France"],
            ]}
          />
          <TextField
            id="content-language"
            label="Content language"
            value={values.content_languages[0] ?? ""}
            placeholder="tr-TR"
            error={fieldErrors.content_languages}
            onChange={(value) => update("content_languages", [value])}
          />
          <TextField
            id="timezone"
            label="Timezone"
            value={values.timezone}
            placeholder="Europe/Istanbul"
            error={fieldErrors.timezone}
            onChange={(value) => update("timezone", value)}
          />
          <TextField
            id="target-cities"
            label="Target cities"
            value={values.target_cities.join(", ")}
            placeholder="Istanbul, Ankara"
            onChange={(value) => update("target_cities", value.split(",").map((item) => item.trim()))}
          />
          <TextField
            id="niche"
            label="Creator niche"
            value={values.niches[0] ?? ""}
            placeholder="Sustainable city travel"
            error={fieldErrors.niches}
            onChange={(value) => update("niches", [value])}
          />
        </fieldset>

        <fieldset className="mt-8">
          <legend className="section-title">Primary goals</legend>
          <p id="goals-hint" className="field-hint">Choose one or more outcomes.</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {GOALS.map((goal) => (
              <label key={goal} className="toggle-row justify-start! gap-3!">
                <input
                  type="checkbox"
                  checked={values.goals.includes(goal)}
                  onChange={() => toggleGoal(goal)}
                  aria-describedby={fieldErrors.goals ? "goals-error" : "goals-hint"}
                />
                <span className="text-sm font-medium text-slate-700">{goal}</span>
              </label>
            ))}
          </div>
          {fieldErrors.goals && <p id="goals-error" className="field-error">{fieldErrors.goals}</p>}
        </fieldset>

        <div className="mt-8">
          <label className="label" htmlFor="constraints">Production constraints</label>
          <textarea
            className="input min-h-28"
            id="constraints"
            value={constraints}
            onChange={(event) => setConstraints(event.target.value)}
            placeholder={"One constraint per line, e.g.\nNo studio\nOne-person crew"}
          />
          <p className="field-hint">Optional. Include budget, locations, equipment, or timing.</p>
        </div>

        <div className="mt-8 flex justify-end">
          <button className="button button-primary" disabled={submitting}>
            {submitting && <span className="spinner spinner-light" aria-hidden="true" />}
            {submitting ? "Saving setup..." : "Save and continue"}
          </button>
        </div>
      </form>
    </main>
  );
}

function TextField(props: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  const errorId = `${props.id}-error`;
  return (
    <div>
      <label className="label" htmlFor={props.id}>{props.label}</label>
      <input
        className="input"
        id={props.id}
        value={props.value}
        placeholder={props.placeholder}
        aria-invalid={Boolean(props.error)}
        aria-describedby={props.error ? errorId : undefined}
        onChange={(event) => props.onChange(event.target.value)}
      />
      {props.error && <p className="field-error" id={errorId}>{props.error}</p>}
    </div>
  );
}

function SelectField(props: {
  id: string;
  label: string;
  value: string;
  error?: string;
  options: Array<[string, string]>;
  onChange: (value: string) => void;
}) {
  const errorId = `${props.id}-error`;
  return (
    <div>
      <label className="label" htmlFor={props.id}>{props.label}</label>
      <select
        className="input"
        id={props.id}
        value={props.value}
        aria-invalid={Boolean(props.error)}
        aria-describedby={props.error ? errorId : undefined}
        onChange={(event) => props.onChange(event.target.value)}
      >
        {props.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      {props.error && <p className="field-error" id={errorId}>{props.error}</p>}
    </div>
  );
}
