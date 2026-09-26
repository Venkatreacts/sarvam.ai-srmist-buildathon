export type DiffTok = { t: string; op: "same" | "del" | "add" };

/** Token-level LCS diff between what the caller said and what the agent heard. */
export function wordDiff(a: string, b: string): DiffTok[] {
  const x = a.split(/\s+/).filter(Boolean);
  const y = b.split(/\s+/).filter(Boolean);
  const norm = (s: string) => s.replace(/[.,!?।॥"'“”]/g, "").toLowerCase();
  const n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = norm(x[i]) === norm(y[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: DiffTok[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (norm(x[i]) === norm(y[j])) { out.push({ t: y[j], op: "same" }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ t: x[i++], op: "del" });
    else out.push({ t: y[j++], op: "add" });
  }
  while (i < n) out.push({ t: x[i++], op: "del" });
  while (j < m) out.push({ t: y[j++], op: "add" });
  return out;
}
