import { safeParse } from '$lib/utils';
import { listTasks } from './api';
import type { AppStores } from '$lib/stores';
import type { BackgroundTaskInfo } from '$lib/stores/features/tasks.svelte';
import type { StateUpdate } from '$lib/types';

type SessionStreamStores = Pick<AppStores, 'tasksStore' | 'notificationsStore'>;

/**
 * Payload for steering message requests.
 */
interface SteeringMessagePayload {
  text: string;
  active_tasks: BackgroundTaskInfo[];
}

/**
 * Request body for the /steer endpoint.
 */
interface SteerRequestBody {
  session_id: string;
  task_id: string;
  event_type: string;
  payload: SteeringMessagePayload;
  source?: string;
}

/**
 * Response from the /steer endpoint.
 */
interface SteerResponse {
  accepted: boolean;
}

/**
 * Send a USER_MESSAGE steering event to redirect or provide input to an active task.
 *
 * @param sessionId - The current session ID
 * @param taskId - The task ID to steer (typically the foreground task)
 * @param text - The user message text
 * @param activeTasks - Array of currently active background tasks for context
 * @returns Promise<boolean> - true if the steering event was accepted
 */
export async function sendSteeringMessage(
  sessionId: string,
  taskId: string,
  text: string,
  activeTasks: BackgroundTaskInfo[]
): Promise<boolean> {
  try {
    const body: SteerRequestBody = {
      session_id: sessionId,
      task_id: taskId,
      event_type: 'USER_MESSAGE',
      payload: {
        text,
        active_tasks: activeTasks
      },
      source: 'user'
    };

    const response = await fetch('/steer', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      console.error('Steering message failed:', response.statusText);
      return false;
    }

    const data: SteerResponse = await response.json();
    return data.accepted;
  } catch (err) {
    console.error('Failed to send steering message:', err);
    return false;
  }
}

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
      // Skip events that don't have update_type (e.g., "connected" event)
      if (data.update_type == null) {
        return;
      }
      const update = data as StateUpdate;
      // Ensure task_id is present before applying task updates
      if (!update.task_id) {
        return;
      }
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
