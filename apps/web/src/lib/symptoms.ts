// Symptom checkboxes on the patient check-in form. Placeholders -- keys match
// apps/api's core/rules.py PLACEHOLDER_RULES, not Sarah's actual red-flag list
// yet. Replace here and in the api rules together once that list exists.
export const SYMPTOM_OPTIONS: { key: string; label: string }[] = [
  { key: "fever", label: "Fever or chills" },
  { key: "heavy_bleeding", label: "Heavy or worsening bleeding" },
  { key: "severe_pain", label: "Pain that's getting worse, not better" },
  { key: "wound_opening", label: "The incision has opened or is leaking" },
];

/** Labels of the symptoms the patient ticked (unknown keys shown as-is). */
export function reportedSymptoms(answers: Record<string, unknown>): string[] {
  return Object.entries(answers)
    .filter(([, v]) => v === true)
    .map(([k]) => SYMPTOM_OPTIONS.find((o) => o.key === k)?.label ?? k);
}
