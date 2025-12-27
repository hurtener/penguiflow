/**
 * Represents a stored artifact reference with metadata.
 * Used for tracking downloadable files generated during agent execution.
 */
export interface ArtifactRef {
  /** Unique artifact identifier */
  id: string;
  /** MIME type of the artifact (e.g., "application/pdf", "image/png") */
  mime_type: string | null;
  /** Size of the artifact in bytes */
  size_bytes: number | null;
  /** Original filename of the artifact */
  filename: string | null;
  /** SHA256 hash of the artifact content */
  sha256: string | null;
  /** Source metadata (tool name, parameters, etc.) */
  source: Record<string, unknown>;
}

/**
 * SSE event payload for artifact_stored events.
 * Emitted when a tool successfully stores an artifact.
 */
export interface ArtifactStoredEvent {
  /** Unique artifact identifier */
  artifact_id: string;
  /** MIME type of the artifact */
  mime_type: string;
  /** Size of the artifact in bytes */
  size_bytes: number;
  /** Original filename of the artifact */
  filename: string;
  /** Source metadata (tool name, parameters, etc.) */
  source: Record<string, unknown>;
  /** Trace ID of the execution that created this artifact */
  trace_id: string;
  /** Session ID the artifact belongs to */
  session_id: string;
  /** Timestamp when the artifact was stored */
  ts: number;
}
