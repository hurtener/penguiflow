import { safeParse } from '$lib/utils';
import { listTasks } from './api';
import type { AppStores } from '$lib/stores';
import type { StateUpdate } from '$lib/types';

type SessionStreamStores = Pick<AppStores, 'tasksStore' | 'notificationsStore'>;

class SessionStreamManager {
  constructor(private stores: SessionStreamStores) {}
  private eventSource: EventSource | null = null;
  private lastUpdateId: string | null = null;
  private activeSessionId: string | null = null;

  start(sessionId: string): void {
    this.close();
    if (this.activeSessionId !== sessionId) {
      this.lastUpdateId = null;
    }
    this.activeSessionId = sessionId;
    listTasks(sessionId).then(tasks => {
      if (tasks) {
        this.stores.tasksStore.setTasks(tasks);
      }
    });
    const url = new URL('/session/stream', window.location.origin);
    url.searchParams.set('session_id', sessionId);
    if (this.lastUpdateId) {
      url.searchParams.set('since_id', this.lastUpdateId);
    }
    this.eventSource = new EventSource(url.toString());

    const handler = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;
      if (evt.type !== 'state_update' && data.update_type == null) {
        return;
      }
      const update = data as StateUpdate;
      this.lastUpdateId = update.update_id ?? this.lastUpdateId;
      this.stores.tasksStore.applyUpdate(update);
      if (update.update_type === 'NOTIFICATION') {
        const content = update.content;
        if (content && typeof content === 'object' && !Array.isArray(content)) {
          const severityRaw = String((content as Record<string, unknown>).severity ?? 'info');
          const severity = ['info', 'success', 'warning', 'error'].includes(severityRaw)
            ? severityRaw
            : 'info';
          const body = String((content as Record<string, unknown>).body ?? '');
          const title = String((content as Record<string, unknown>).title ?? '');
          const message = title ? `${title}: ${body}` : body;
          const actionsRaw = (content as Record<string, unknown>).actions;
          const actions = Array.isArray(actionsRaw)
            ? actionsRaw
                .filter(item => item && typeof item === 'object')
                .map(item => ({
                  id: String((item as Record<string, unknown>).id ?? ''),
                  label: String((item as Record<string, unknown>).label ?? 'Action'),
                  payload: (item as Record<string, unknown>).payload as Record<string, unknown> | undefined
                }))
                .filter(item => item.id)
            : undefined;
          this.stores.notificationsStore.add(message || 'Notification', severity as any, actions);
        }
      }
    };

    this.eventSource.addEventListener('state_update', handler);
    this.eventSource.onmessage = handler;
    this.eventSource.onerror = () => this.close();
  }

  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.activeSessionId = null;
  }
}

export function createSessionStreamManager(stores: SessionStreamStores): SessionStreamManager {
  return new SessionStreamManager(stores);
}
