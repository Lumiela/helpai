export const IRRIGATION_DURATION_MS = 180_000;
export const SIMULATED_IRRIGATION_DURATION_MS = 5_000;

// App이 소유하므로 큐브/페이지 전환 뒤에도 유지한다.
export class IrrigationTimers {
  private timers = new Map<string, ReturnType<typeof setTimeout>>();

  start(zoneId: string, simulated: boolean, onComplete: () => void) {
    this.cancel(zoneId);
    this.timers.set(zoneId, setTimeout(() => {
      this.timers.delete(zoneId);
      onComplete();
    }, simulated ? SIMULATED_IRRIGATION_DURATION_MS : IRRIGATION_DURATION_MS));
  }

  cancel(zoneId: string) {
    const timer = this.timers.get(zoneId);
    if (timer !== undefined) clearTimeout(timer);
    this.timers.delete(zoneId);
  }

  cancelAll() {
    for (const zoneId of this.timers.keys()) this.cancel(zoneId);
  }
}
