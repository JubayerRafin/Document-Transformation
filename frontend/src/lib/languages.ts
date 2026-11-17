// The 11 languages the engine translates between (transformation/pdf/runner.py LANGUAGES).
export const LANGUAGES: [string, string][] = [
  ["en", "English"],
  ["ko", "Korean"],
  ["ja", "Japanese"],
  ["zh", "Chinese (Simplified)"],
  ["ar", "Arabic"],
  ["hi", "Hindi"],
  ["bn", "Bengali"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
  ["pt", "Portuguese"],
];
export function languageName(code: string) {
  return LANGUAGES.find(([c]) => c === code)?.[1] || code;
}
