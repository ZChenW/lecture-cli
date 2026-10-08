// Keyboard model of the custom dropdown (N2.1), kept free of the DOM so it can be tested directly.

export interface ListOption { value: string; label: string; code?: string; disabled?: boolean }

/** The next enabled index for a navigation key; null when the key does not move. */
export function moveIndex(options: ListOption[], current: number, key: string): number | null {
  const enabled = options.map((option, i) => (option.disabled ? -1 : i)).filter((i) => i >= 0);
  if (!enabled.length) return null;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled.at(-1)!;
  if (key === "ArrowDown") return enabled.find((i) => i > current) ?? (current < 0 ? enabled[0] : current);
  if (key === "ArrowUp") return [...enabled].reverse().find((i) => i < current) ?? (current < 0 ? enabled.at(-1)! : current);
  return null;
}

/**
 * Type-ahead: the first enabled option after the current one whose label or code starts with the
 * typed text, wrapping around. Repeating one letter cycles through the options it matches.
 */
export function matchIndex(options: ListOption[], current: number, typed: string): number | null {
  const text = typed.toLowerCase();
  if (!text) return null;
  const cycling = [...text].every((c) => c === text[0]);
  const needle = cycling ? text[0] : text;
  const start = cycling || text.length === 1 ? current + 1 : current;
  for (let step = 0; step < options.length; step++) {
    const i = (Math.max(start, 0) + step) % options.length;
    const option = options[i];
    if (option.disabled) continue;
    if ([option.label, option.code ?? "", option.value].some((s) => s.toLowerCase().startsWith(needle))) return i;
  }
  return null;
}

/** Opens upward when the list would run past the bottom of the window and there is more room above. */
export function opensUpward(trigger: { top: number; bottom: number }, listHeight: number, viewport: number, gap = 6): boolean {
  const below = viewport - trigger.bottom - gap;
  const above = trigger.top - gap;
  return listHeight > below && above > below;
}
