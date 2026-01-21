# LLM Logging Levels

Some LLM-layer retry logs were originally raised to INFO during the native client
bring-up. They have been moved back to DEBUG to keep normal runs quieter.

If you need them at INFO again, edit:
- `penguiflow/llm/telemetry.py` (retry event logging)
- `penguiflow/llm/retry.py` (ModelRetry logging)

Change the `logger.debug(...)` calls back to `logger.info(...)` in those spots.

## Planner Debug Logs

The ReactPlanner emits several debug-only trace logs for troubleshooting. These
were moved from INFO to DEBUG:
- `penguiflow/planner/react_step.py`: `DEBUG_step_messages`, `DEBUG_llm_raw_response`
- `penguiflow/planner/react_runtime.py`: `planner_action`, `finish_candidate_answer`,
  `finish_pre_payload`, `finish_payload_built`

To re-enable them at INFO, change `logger.debug(...)` back to `logger.info(...)`
for those events.
