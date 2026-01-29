import { createChatStore, getChatStore, setChatStore } from './domain/chat.svelte';
import type { ChatStore } from './domain/chat.svelte';
import { createSessionStore, getSessionStore, setSessionStore } from './domain/session.svelte';
import type { SessionStore } from './domain/session.svelte';
import { createArtifactsStore, getArtifactsStore, setArtifactsStore } from './domain/artifacts.svelte';
import type { ArtifactsStore } from './domain/artifacts.svelte';
import { createAgentStore, getAgentStore, setAgentStore } from './domain/agent.svelte';
import type { AgentStore } from './domain/agent.svelte';
import { createEventsStore, getEventsStore, setEventsStore } from './features/events.svelte';
import type { EventsStore } from './features/events.svelte';
import { createTasksStore, getTasksStore, setTasksStore } from './features/tasks.svelte';
import type { TasksStore } from './features/tasks.svelte';
import { createTrajectoryStore, getTrajectoryStore, setTrajectoryStore } from './features/trajectory.svelte';
import type { TrajectoryStore } from './features/trajectory.svelte';
import { createSpecStore, getSpecStore, setSpecStore } from './features/spec.svelte';
import type { SpecStore } from './features/spec.svelte';
import { createSetupStore, getSetupStore, setSetupStore } from './features/setup.svelte';
import type { SetupContext, SetupStore } from './features/setup.svelte';
import { createInteractionsStore, getInteractionsStore, setInteractionsStore } from './features/interactions.svelte';
import type { InteractionsStore } from './features/interactions.svelte';
import { createComponentRegistryStore, getComponentRegistryStore, setComponentRegistryStore } from './features/component-registry.svelte';
import type { ComponentRegistryStore } from './features/component-registry.svelte';
import { createLayoutStore, getLayoutStore, setLayoutStore } from './ui/layout.svelte';
import type { LayoutStore } from './ui/layout.svelte';
import { createNotificationsStore, getNotificationsStore, setNotificationsStore } from './ui/notifications.svelte';
import type { NotificationsStore } from './ui/notifications.svelte';

export {
  createChatStore,
  getChatStore,
  setChatStore,
  createSessionStore,
  getSessionStore,
  setSessionStore,
  createArtifactsStore,
  getArtifactsStore,
  setArtifactsStore,
  createAgentStore,
  getAgentStore,
  setAgentStore,
  createEventsStore,
  getEventsStore,
  setEventsStore,
  createTasksStore,
  getTasksStore,
  setTasksStore,
  createTrajectoryStore,
  getTrajectoryStore,
  setTrajectoryStore,
  createSpecStore,
  getSpecStore,
  setSpecStore,
  createSetupStore,
  getSetupStore,
  setSetupStore,
  createInteractionsStore,
  getInteractionsStore,
  setInteractionsStore,
  createComponentRegistryStore,
  getComponentRegistryStore,
  setComponentRegistryStore,
  createLayoutStore,
  getLayoutStore,
  setLayoutStore,
  createNotificationsStore,
  getNotificationsStore,
  setNotificationsStore
};

export type {
  ChatStore,
  SessionStore,
  ArtifactsStore,
  AgentStore,
  EventsStore,
  TasksStore,
  TrajectoryStore,
  SpecStore,
  SetupStore,
  SetupContext,
  InteractionsStore,
  ComponentRegistryStore,
  LayoutStore,
  NotificationsStore
};

export interface AppStores {
  chatStore: ChatStore;
  sessionStore: SessionStore;
  artifactsStore: ArtifactsStore;
  agentStore: AgentStore;
  eventsStore: EventsStore;
  tasksStore: TasksStore;
  trajectoryStore: TrajectoryStore;
  specStore: SpecStore;
  setupStore: SetupStore;
  interactionsStore: InteractionsStore;
  componentRegistryStore: ComponentRegistryStore;
  layoutStore: LayoutStore;
  notificationsStore: NotificationsStore;
}

export function initStores(): AppStores {
  return {
    chatStore: setChatStore(),
    sessionStore: setSessionStore(),
    artifactsStore: setArtifactsStore(),
    agentStore: setAgentStore(),
    eventsStore: setEventsStore(),
    tasksStore: setTasksStore(),
    trajectoryStore: setTrajectoryStore(),
    specStore: setSpecStore(),
    setupStore: setSetupStore(),
    interactionsStore: setInteractionsStore(),
    componentRegistryStore: setComponentRegistryStore(),
    layoutStore: setLayoutStore(),
    notificationsStore: setNotificationsStore()
  };
}
