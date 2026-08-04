export const refreshRotationMilliseconds = 800;

export function delayUntilRefreshRotationEnds(startedAt: number, completedAt: number): number {
  const elapsed = Math.max(0, completedAt - startedAt);
  if (elapsed < refreshRotationMilliseconds) {
    return refreshRotationMilliseconds - elapsed;
  }
  const partialRotation = elapsed % refreshRotationMilliseconds;
  return partialRotation === 0 ? 0 : refreshRotationMilliseconds - partialRotation;
}
