export interface ReservationPolicyMutationGuard {
  snapshot: () => number;
  begin: () => void;
  end: () => void;
  isCurrent: (snapshot: number) => boolean;
}

export function createReservationPolicyMutationGuard(): ReservationPolicyMutationGuard {
  let epoch = 0;

  return {
    snapshot: () => epoch,
    begin: () => { epoch += 1; },
    end: () => { epoch += 1; },
    isCurrent: (snapshot) => snapshot === epoch,
  };
}
