# Inflect interface refinement

## Delivered

- Live GPU power, CPU package power, VRAM, RAM, and CPU usage.
- Prompt-processing and decode tokens per second shown separately, alongside time to first token.
- Visible profile actions for creating, loading, editing, renaming, copying, deleting, undoing, and restoring.
- Screenshot paste with Ctrl+V, ordinary text paste, image previews/removal, drag-and-drop, and vision-checkpoint validation.
- Tail following during streaming, automatic pause after the user scrolls away, and a **Jump to latest** action.
- Copyable answers, a growing message box, per-model temporary conversations, keyboard focus, responsive layouts, and restrained accent colors.

## Verification checklist

- Backend tests cover profile migration, naming conflicts, editing, deletion/restoration, local API protections, unavailable power sensors, and energy-counter rollover.
- TypeScript checking and the production build complete without errors.
- A temporary profile can be created, renamed, changed, loaded, deleted, restored, and removed without altering existing profiles.
- A pasted screenshot reaches a loaded vision model and displays an image preview before sending.
- Scrolling away from a growing response pauses tail following; **Jump to latest** resumes it.
- Stop leaves the partial answer visible and the model usable.
- Ordinary text paste remains text, and an image with a text-only checkpoint produces an actionable message.
- Narrow layouts do not create horizontal page overflow.
- Model details and engine logs remain accessible after a load failure.

Machine-generated evidence contains local paths, hardware data, prompts, output, and profile names, so it remains local under ignored `validation/` files. See the repository-level [validation procedure](../VALIDATION.md) for reproducible checks.
