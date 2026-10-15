use std::fs;

use assert_cmd::Command;
use tempfile::tempdir;

const WORKER_LIVE_YAML: &str = r#"
role: worker
jenkins_agent:
  controller_url: http://43.107.42.252:17070
  name: linux-eval-1
  labels:
    - linux
    - docker
  remote_fs: /home/src/jenkins-agent
  cpu_cores: 8
  api_user: admin
  api_token: test-jenkins-token
dependencies:
  harbor:
    source: skip
lolbench:
  source: skip
resources:
  cpu_cores_label: CPU_CORES
"#;

#[test]
fn help_lists_setup_subcommand() {
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .arg("--help")
        .assert()
        .success();
    let stdout = String::from_utf8_lossy(&assert.get_output().stdout);
    assert!(
        stdout.contains("setup"),
        "help should list setup:\n{stdout}"
    );
    assert!(stdout.contains("prepare"));
    assert!(stdout.contains("eval"), "help should list eval:\n{stdout}");
    assert!(
        stdout.contains("export"),
        "help should list export:\n{stdout}"
    );
    assert!(
        stdout.contains("import"),
        "help should list import:\n{stdout}"
    );
    assert!(stdout.contains("set"), "help should list set:\n{stdout}");
}

#[test]
fn version_names_the_commit_it_was_built_from() {
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .arg("--version")
        .assert()
        .success();
    let out = String::from_utf8_lossy(&assert.get_output().stdout).to_string();
    let short: String = mac_k3d::build_info::COMMIT.chars().take(12).collect();
    assert!(out.starts_with(&format!("mac-k3d {} ({short}", env!("CARGO_PKG_VERSION"))), "{out}");
}

#[test]
fn pipeline_extract_needs_no_config() {
    let home = tempdir().unwrap();
    let dest = home.path().join("ws/mac-k3d-pipeline");
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .env("HOME", home.path())
        .env_remove("XDG_CONFIG_HOME")
        .args(["pipeline", "--extract-to"])
        .arg(&dest)
        .assert()
        .success();
    let out = String::from_utf8_lossy(&assert.get_output().stdout).to_string();
    assert!(out.starts_with(&format!("mac-k3d pipeline {}", mac_k3d::build_info::COMMIT)), "{out}");
    assert!(dest.join("pipeline/stages/run_all.sh").is_file());
    let build: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(dest.join("pipeline/BUILD.json")).unwrap()).unwrap();
    assert_eq!(build["commit"], mac_k3d::build_info::COMMIT);
    assert_eq!(build["pipeline_hash"], mac_k3d::build_info::PIPELINE_HASH);
}

#[test]
fn missing_explicit_config_points_at_setup() {
    let dir = tempdir().unwrap();
    let missing = dir.path().join("worker.yaml");
    for cmd in ["config", "status", "start", "eval"] {
        Command::cargo_bin("mac-k3d")
            .unwrap()
            .env("HOME", dir.path())
            .args([cmd, "-c"])
            .arg(&missing)
            .assert()
            .failure()
            .stderr(predicates::str::contains(format!(
                "no config at {m}; run `mac-k3d setup -c {m}` first",
                m = missing.display()
            )));
    }
}

#[test]
fn setup_help_describes_wizard() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["setup", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("wizard"));
}

#[test]
fn eval_help_mentions_stage() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("stage"));
}

#[test]
fn eval_help_mentions_model() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("model"))
        .stdout(predicates::str::contains("icode-git-url"))
        .stdout(predicates::str::contains("icode-git-ref"))
        .stdout(predicates::str::contains("icode-git-ref-kind"));
}

#[test]
fn eval_help_mentions_the_jenkins_queue_flags() {
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--help"])
        .assert()
        .success();
    let stdout = String::from_utf8_lossy(&assert.get_output().stdout);
    for flag in ["--job", "--tasks", "--n-rollouts", "--param", "--dry-run"] {
        assert!(stdout.contains(flag), "{flag} missing:\n{stdout}");
    }
}

#[test]
fn eval_job_dry_run_prints_only_the_given_fields() {
    let home = tempdir().unwrap();
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .env("HOME", home.path())
        .env_remove("XDG_CONFIG_HOME")
        .args([
            "eval", "--job", "some", "--tasks", "a, b", "--n-rollouts", "1", "--dry-run",
        ])
        .assert()
        .success();
    let out = String::from_utf8_lossy(&assert.get_output().stdout).to_string();
    assert!(out.starts_with("Queue deepswe_some_task with:\n  TASKS=a,b\n  N_ROLLOUTS=1\n"), "{out}");
    assert!(out.contains("Dry run: nothing queued."), "{out}");
}

#[test]
fn eval_job_refuses_local_and_lone_queue_flags() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--local", "--job", "one"])
        .assert()
        .code(2)
        .stderr(predicates::str::contains("cannot be used with"));
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--tasks", "a,b"])
        .assert()
        .code(2)
        .stderr(predicates::str::contains("--job"));
}

#[test]
fn eval_help_mentions_benchmark_and_task() {
    let assert = Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["eval", "--help"])
        .assert()
        .success();
    let stdout = String::from_utf8_lossy(&assert.get_output().stdout);
    assert!(stdout.contains("benchmark"), "{stdout}");
    assert!(stdout.contains("task"), "{stdout}");
}

#[test]
fn no_subcommand_without_tty_exits_2() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .write_stdin("")
        .assert()
        .code(2)
        .stderr(predicates::str::contains("not a TTY"));
}

#[test]
fn start_help_mentions_cluster() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["start", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("k3d"));
}

#[test]
fn export_help_requires_output() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["export", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("--output"));
}

#[test]
fn import_help_mentions_force() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["import", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("--force"));
}

#[test]
fn set_help_lists_catalog_flags() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["set", "--help"])
        .assert()
        .success()
        .stdout(predicates::str::contains("--harness"))
        .stdout(predicates::str::contains("--n-tasks"))
        .stdout(predicates::str::contains("--list"))
        .stdout(predicates::str::contains("--model"))
        .stdout(predicates::str::contains("--check-models"));
}

#[test]
fn set_list_prints_catalog_models() {
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args(["set", "--list"])
        .assert()
        .success()
        .stdout(predicates::str::contains("deepseek-v4-pro"))
        .stdout(predicates::str::contains("deepseek-flash"))
        .stdout(predicates::str::contains("GET"))
        .stdout(predicates::str::contains("--check-models"));
}

#[test]
fn set_check_models_requires_key() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("config.yaml");
    fs::write(&path, "role: controller\n").unwrap();
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .current_dir(dir.path())
        .env_remove("DEEPSEEK_API_KEY")
        .env_remove("MAC_K3D_DEEPSEEK_API_KEY")
        .env(
            "MAC_K3D_ENV_FILE",
            dir.path().join("missing.env").to_str().unwrap(),
        )
        .env("XDG_CONFIG_HOME", dir.path().join("xdg").to_str().unwrap())
        .env("HOME", dir.path().to_str().unwrap())
        .args(["set", "-c", path.to_str().unwrap(), "--check-models"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("DEEPSEEK_API_KEY missing"));
}

#[test]
fn set_check_models_rejects_unknown_catalog_model_before_api() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("config.yaml");
    fs::write(&path, "role: controller\n").unwrap();
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .current_dir(dir.path())
        .env_remove("DEEPSEEK_API_KEY")
        .args([
            "set",
            "-c",
            path.to_str().unwrap(),
            "--model",
            "gpt-4",
            "--check-models",
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains("allowed: deepseek-flash, deepseek-v4-pro"));
}

#[test]
fn set_model_writes_yaml() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("config.yaml");
    fs::write(&path, "role: controller\n").unwrap();
    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "set",
            "-c",
            path.to_str().unwrap(),
            "--model",
            "deepseek-flash",
        ])
        .assert()
        .success();
    let yaml = fs::read_to_string(&path).unwrap();
    assert!(
        yaml.contains("default_deepseek_model: deepseek-flash"),
        "{yaml}"
    );
}

#[test]
fn export_import_worker_yaml_scratch_dest() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("worker.yaml");
    let portable = dir.path().join("portable.yaml");
    let scratch = dir.path().join("imported-worker.yaml");
    fs::write(&src, WORKER_LIVE_YAML).unwrap();

    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "export",
            "-c",
            src.to_str().unwrap(),
            "-o",
            portable.to_str().unwrap(),
        ])
        .assert()
        .success();
    let portable_text = fs::read_to_string(&portable).unwrap();
    assert!(
        portable_text.contains("api_token: ''"),
        "export must blank api_token:\n{portable_text}"
    );
    assert!(
        !portable_text.contains("test-jenkins-token"),
        "{portable_text}"
    );
    assert!(
        portable_text.contains("http://43.107.42.252:17070"),
        "{portable_text}"
    );
    assert!(portable_text.contains("linux-eval-1"), "{portable_text}");
    assert!(portable_text.contains("linux"), "{portable_text}");

    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "import",
            portable.to_str().unwrap(),
            "-c",
            scratch.to_str().unwrap(),
        ])
        .assert()
        .success()
        .stdout(predicates::str::contains("write only"))
        .stdout(predicates::str::contains("api_token"))
        .stdout(predicates::str::contains("config -c"));

    let scratch_text = fs::read_to_string(&scratch).unwrap();
    assert!(scratch_text.contains("role: worker"), "{scratch_text}");
    assert!(
        scratch_text.contains("http://43.107.42.252:17070"),
        "{scratch_text}"
    );
    assert!(
        scratch_text.contains("api_token: ''"),
        "scratch import must leave api_token blank:\n{scratch_text}"
    );
    assert!(
        !scratch_text.contains("test-jenkins-token"),
        "{scratch_text}"
    );

    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "import",
            portable.to_str().unwrap(),
            "-c",
            scratch.to_str().unwrap(),
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains("already exists"));
}

#[test]
fn import_force_strips_dest_worker_token() {
    let dir = tempdir().unwrap();
    let src = dir.path().join("worker.yaml");
    let portable = dir.path().join("portable.yaml");
    let dest = dir.path().join("dest-worker.yaml");
    fs::write(&src, WORKER_LIVE_YAML).unwrap();
    fs::write(
        &dest,
        "role: worker\njenkins_agent:\n  api_token: leftover-token\n",
    )
    .unwrap();

    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "export",
            "-c",
            src.to_str().unwrap(),
            "-o",
            portable.to_str().unwrap(),
        ])
        .assert()
        .success();

    Command::cargo_bin("mac-k3d")
        .unwrap()
        .args([
            "import",
            portable.to_str().unwrap(),
            "-c",
            dest.to_str().unwrap(),
            "--force",
        ])
        .assert()
        .success();

    let dest_text = fs::read_to_string(&dest).unwrap();
    assert!(!dest_text.contains("leftover-token"), "{dest_text}");
    assert!(
        dest_text.contains("api_token: ''"),
        "import --force must blank the dest api_token:\n{dest_text}"
    );
    assert!(
        dest_text.contains("http://43.107.42.252:17070"),
        "{dest_text}"
    );
}
