# Lab runbooks

These pages are **this team’s copy-paste checklists** (cloud IP, this PC, sign-off tables). They are not the product user manual.

**Users:** [user-guide.md](../user-guide.md).

| File | Use |
|------|-----|
| [testing-eval-pipeline.md](testing-eval-pipeline.md) | E0–E8 / pipeline phases |
| [cloud-eval-runbook.md](cloud-eval-runbook.md) | This lab’s controller IP + worker |
| [mac-mini-worker.md](mac-mini-worker.md) | Onboard a Mac mini worker, then compare it with the Linux worker on the same questions |
| [testing-binary-initializer.md](testing-binary-initializer.md) | Bootstrap Task 0–8 |
| [clean-machine-binary-test.md](clean-machine-binary-test.md) | Wipe or new PC |
| [question-coverage.md](question-coverage.md) | Questions run on this worker, and Docker memory per question |
| [question-log.md](question-log.md) | Per-run result of every question, open problems and your fix notes (`mac-k3d eval record`) |
| [nas-storage.md](nas-storage.md) | What to copy to a NAS after a run, and what stays on the worker |

Do not nest a second `testing/` under a branch folder. Branch re-test notes stay in `docs/<branch>/testing.md`.
