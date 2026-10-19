// Who may use the AI assistant on which project (TEX-02, D8–D11), and the switch that turns it
// off immediately: running answers are aborted and open editors get {type: 'ai', enabled}.
import { config } from './config.js';
import { all, one, run } from './db.js';
import { broadcast } from './collab.js';
import { llmEnabled } from './llm/index.js';

/**
 * Whether the assistant is available for a project row (role checks are separate).
 * Returns null when allowed, otherwise the reason: 'not_configured' (LLM_ENABLED off or no
 * providers) or 'ai_disabled' (locked by the organiser).
 */
export function aiBlockReason(project) {
  if (!llmEnabled()) return 'not_configured';
  if (project.ai_locked) return 'ai_disabled';
  // On the olympiad server only platform projects exist, and only an explicit `ai` claim unlocks.
  if (config.integration.enabled && !one(`SELECT 1 FROM linked_projects WHERE project_id = ?`, project.id)) return 'ai_disabled';
  return null;
}

export const canUseAi = (project, role) => (role === 'owner' || role === 'editor') && !aiBlockReason(project);

// ---------- running answers ----------

const active = new Map(); // projectId -> Set<AbortController>

/** Registers a running answer; returns the function that unregisters it. */
export function trackAiRequest(projectId, controller) {
  let set = active.get(projectId);
  if (!set) active.set(projectId, (set = new Set()));
  set.add(controller);
  return () => {
    set.delete(controller);
    if (!set.size && active.get(projectId) === set) active.delete(projectId);
  };
}

export function abortAiRequests(projectId) {
  for (const c of active.get(projectId) || []) c.abort(new Error('ai_disabled'));
}

// ---------- organiser's switch ----------

/** Applies a lock value to projects, notifying open editors of every project whose value changed. */
export function setAiLocked(projectIds, locked) {
  let changed = 0;
  for (const id of projectIds) {
    const r = run(`UPDATE projects SET ai_locked = ? WHERE id = ? AND ai_locked != ?`, locked ? 1 : 0, id, locked ? 1 : 0);
    if (!Number(r.changes)) continue;
    changed++;
    if (locked) abortAiRequests(id);
    broadcast(id, { type: 'ai', enabled: !locked && !aiBlockReason(one(`SELECT * FROM projects WHERE id = ?`, id)) });
  }
  return changed;
}

export function setAiLockedForScope(scope, locked) {
  const ids = all(`SELECT project_id FROM linked_projects WHERE scope = ?`, scope).map((r) => r.project_id);
  return setAiLocked(ids, locked);
}
