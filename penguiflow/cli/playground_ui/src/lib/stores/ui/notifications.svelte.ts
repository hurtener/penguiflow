import { getContext, setContext } from 'svelte';

const NOTIFICATIONS_STORE_KEY = Symbol('notifications-store');

export type NotificationLevel = 'info' | 'success' | 'warning' | 'error';

export interface Notification {
  id: string;
  message: string;
  level: NotificationLevel;
  ts: number;
}

export interface NotificationsStore {
  readonly items: Notification[];
  add(message: string, level?: NotificationLevel): Notification;
  remove(id: string): void;
  clear(): void;
}

export function createNotificationsStore(): NotificationsStore {
  let items = $state<Notification[]>([]);

  return {
    get items() { return items; },
    add(message: string, level: NotificationLevel = 'info') {
      const note: Notification = {
        id: `note_${Date.now()}_${Math.random().toString(16).slice(2)}`,
        message,
        level,
        ts: Date.now()
      };
      items = [note, ...items];
      return note;
    },
    remove(id: string) {
      items = items.filter(item => item.id !== id);
    },
    clear() {
      items = [];
    }
  };
}

export function setNotificationsStore(
  store: NotificationsStore = createNotificationsStore()
): NotificationsStore {
  setContext(NOTIFICATIONS_STORE_KEY, store);
  return store;
}

export function getNotificationsStore(): NotificationsStore {
  return getContext<NotificationsStore>(NOTIFICATIONS_STORE_KEY);
}
