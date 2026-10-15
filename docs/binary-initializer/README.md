# Branch process: `binary-initializer`

This folder is **branch process and re-test notes**, not the user start-here. User docs: [user-guide.md](../user-guide.md), [new-machine.md](../new-machine.md), [icode-harness-inputs.md](../icode-harness-inputs.md). Lab E0–E8: [testing/testing-eval-pipeline.md](../testing/testing-eval-pipeline.md).

## What this branch shipped

1. **Release binary** — users download `mac-k3d-{os}-{arch}` from GitHub Releases. Rust is not required.
2. **`setup` installs Docker** — wizard **Install** on Ubuntu/Debian (`apt`) or macOS (Homebrew cask). Honest leftovers: Linux docker-group logout; macOS first Docker Desktop window.
3. **Controller vs worker** — controller: k3d + Jenkins `:17070`. Worker: inbound agent on host Docker (not a k3d node). One PC can run both YAML files.
4. **Eval jobs** — `deepswe_one_task`, `lolbench_one_task`, and `swebenchpro_one_task` (all Harbor). Product eval path is Jenkins **Build with Parameters**.

Current operator copy-paste: [user-guide.md](../user-guide.md). End-to-end story: [workflow.md](../workflow.md).

## How to re-test

Step-by-step log: [testing.md](testing.md).
