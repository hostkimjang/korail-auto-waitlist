type JsonBoundaryPrimitive = null | boolean | number | string;
type JsonBoundaryValue = JsonBoundaryPrimitive | JsonBoundaryValue[] | { [key: string]: JsonBoundaryValue };

export interface IdentifiedTrainSnapshot {
  id: string;
}

/**
 * Compares only values that can cross a JSON API boundary. Values such as
 * undefined, functions, Date instances, and non-finite numbers fail closed
 * instead of being silently omitted as JSON.stringify would do.
 */
export function jsonBoundaryDeepEqual(left: unknown, right: unknown): boolean {
  if (!isJsonBoundaryValue(left) || !isJsonBoundaryValue(right)) return false;
  return equalJsonBoundaryValues(left, right);
}

export function reconcileTrainSnapshots<T extends IdentifiedTrainSnapshot>(
  previous: T[],
  incoming: T[],
): T[] {
  const previousById = snapshotsByUniqueId(previous);
  const incomingIds = uniqueIds(incoming);
  const reconciled = incoming.map((snapshot) => {
    const previousSnapshot = incomingIds.has(snapshot.id) ? previousById.get(snapshot.id) : undefined;
    return previousSnapshot !== undefined && jsonBoundaryDeepEqual(previousSnapshot, snapshot)
      ? previousSnapshot
      : snapshot;
  });

  const preservesEntireArray = previous.length === reconciled.length
    && previous.every((snapshot, index) => snapshot === reconciled[index]);
  return preservesEntireArray ? previous : reconciled;
}

function snapshotsByUniqueId<T extends IdentifiedTrainSnapshot>(snapshots: T[]): Map<string, T> {
  const duplicateIds = duplicateIdsIn(snapshots);
  return new Map(snapshots.flatMap((snapshot) => duplicateIds.has(snapshot.id) ? [] : [[snapshot.id, snapshot] as const]));
}

function uniqueIds<T extends IdentifiedTrainSnapshot>(snapshots: T[]): Set<string> {
  const duplicateIds = duplicateIdsIn(snapshots);
  return new Set(snapshots.flatMap((snapshot) => duplicateIds.has(snapshot.id) ? [] : [snapshot.id]));
}

function duplicateIdsIn<T extends IdentifiedTrainSnapshot>(snapshots: T[]): Set<string> {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const snapshot of snapshots) {
    if (seen.has(snapshot.id)) duplicates.add(snapshot.id);
    seen.add(snapshot.id);
  }
  return duplicates;
}

function isJsonBoundaryValue(value: unknown): value is JsonBoundaryValue {
  if (value === null || typeof value === "boolean" || typeof value === "string") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonBoundaryValue);
  if (typeof value !== "object") return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  return Object.values(value).every(isJsonBoundaryValue);
}

function equalJsonBoundaryValues(left: JsonBoundaryValue, right: JsonBoundaryValue): boolean {
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") {
    return left === right;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((value, index) => {
      const rightValue = right[index];
      return rightValue !== undefined && equalJsonBoundaryValues(value, rightValue);
    });
  }
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) => {
    if (!Object.prototype.hasOwnProperty.call(right, key)) return false;
    const leftValue = left[key];
    const rightValue = right[key];
    return leftValue !== undefined
      && rightValue !== undefined
      && equalJsonBoundaryValues(leftValue, rightValue);
  });
}
