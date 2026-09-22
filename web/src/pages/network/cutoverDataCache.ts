/**
 * Short-lived cache for cutover list APIs.
 * Switching biz-state / compare / migration / templates remounts each page;
 * without cache every click re-waits on Promise.all network round-trips.
 */

type Entry = { at: number; data: unknown };

const store = new Map<string, Entry>();
const DEFAULT_TTL_MS = 45_000;

export async function cutoverCachedGet<T>(
  key: string,
  fetcher: () => Promise<T>,
  opts?: { force?: boolean; ttlMs?: number },
): Promise<T> {
  const ttl = opts?.ttlMs ?? DEFAULT_TTL_MS;
  if (!opts?.force) {
    const hit = store.get(key);
    if (hit && Date.now() - hit.at < ttl) {
      return hit.data as T;
    }
  }
  const data = await fetcher();
  store.set(key, { at: Date.now(), data });
  return data;
}

/** Return stale immediately (if any), refresh in background and notify. */
export async function cutoverCachedGetSWR<T>(
  key: string,
  fetcher: () => Promise<T>,
  onFresh?: (data: T) => void,
): Promise<T> {
  const hit = store.get(key);
  if (hit) {
    void fetcher()
      .then((data) => {
        store.set(key, { at: Date.now(), data });
        onFresh?.(data);
      })
      .catch(() => {
        /* keep stale */
      });
    return hit.data as T;
  }
  const data = await fetcher();
  store.set(key, { at: Date.now(), data });
  return data;
}

export function invalidateCutoverCache(prefix = ""): void {
  if (!prefix) {
    store.clear();
    return;
  }
  for (const k of [...store.keys()]) {
    if (k.startsWith(prefix)) store.delete(k);
  }
}
