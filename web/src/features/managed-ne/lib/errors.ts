import { formatErr } from "../../../services/api";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

/** Map API detail codes such as `ip_address_exists` or `hop_password_required: a,b` to localized text. */
export function managedNeErrorText(err: unknown, t: Translate): string {
  const raw = formatErr(err);
  const matched = /^([a-z_]+)(?::\s*([\s\S]*))?$/.exec(raw.trim());
  if (!matched) return raw;
  const key = `managedNe.errors.${matched[1]}`;
  const template = t(key);
  if (template === key) return raw;
  const detail = (matched[2] || "").trim();
  if (detail) return template.replace(/\{\{detail\}\}/g, detail);
  // No detail from the API: drop the "(…{{detail}})" or ": {{detail}}" part of the message.
  return template
    .replace(/\s*[（(][^（()）]*\{\{detail\}\}[）)]/g, "")
    .replace(/\s*[:：]\s*\{\{detail\}\}/g, "");
}
