//! Everything setup cannot do as a normal user, collected into one block an
//! administrator can run in one go.

use std::path::Path;

use crate::platform::{self, RootStep};

/// Root commands for packages this user cannot install, in the given order.
pub fn for_packages(names: &[&str]) -> Vec<RootStep> {
    names
        .iter()
        .filter_map(|name| {
            platform::root_install_command(name).map(|command| RootStep {
                why: format!("install {name}"),
                command,
            })
        })
        .collect()
}

/// Run the steps here, for a user who is root or may use sudo.
pub fn run(steps: &[RootStep]) -> crate::error::Result<()> {
    for step in steps {
        println!("As root: {}   # {}", step.command, step.why);
        platform::run_as_root(&step.command)?;
    }
    Ok(())
}

/// The one message setup prints when it stops for root.
pub fn render(steps: &[RootStep], config_path: &Path) -> String {
    let width = steps.iter().map(|s| s.command.len()).max().unwrap_or(0);
    let lines = steps
        .iter()
        .map(|s| format!("  {:width$}   # {}", s.command, s.why))
        .collect::<Vec<_>>()
        .join("\n");
    let c = config_path.display();
    format!(
        "Ask an administrator to run these as root on this machine:\n\n{lines}\n\n\
         Your answers are saved in {c}. Afterwards log out and back in (so a new\n\
         docker group applies), then run:\n  mac-k3d setup -c {c}\n\
         and choose \"Use existing config\"; setup carries on from the installs."
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_lists_every_command_and_how_to_resume() {
        let steps = vec![
            RootStep {
                why: "install java".into(),
                command: "apt-get install -y openjdk-21-jre-headless".into(),
            },
            RootStep {
                why: "let toby use Docker without sudo (then log out and back in)".into(),
                command: "usermod -aG docker toby".into(),
            },
            RootStep {
                why: "keep the Jenkins agent running after you log out".into(),
                command: "loginctl enable-linger toby".into(),
            },
        ];
        let text = render(&steps, Path::new("/home/toby/.config/mac-k3d/worker.yaml"));
        assert!(text.contains("as root"));
        for step in &steps {
            assert!(text.contains(&step.command), "{text}");
        }
        assert!(!text.contains("sudo apt"), "root runs the commands itself: {text}");
        assert!(text.contains("mac-k3d setup -c /home/toby/.config/mac-k3d/worker.yaml"));
        assert!(text.contains("Use existing config"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn linux_packages_map_to_apt_without_sudo() {
        let steps = for_packages(&["java", "git", "python", "harbor"]);
        let cmds: Vec<String> = steps.iter().map(|s| s.command.clone()).collect();
        let java = format!(
            "apt-get install -y openjdk-{}-jre-headless",
            crate::prepare::toolchain::java_major()
        );
        assert_eq!(java, "apt-get install -y openjdk-21-jre-headless");
        assert_eq!(
            cmds,
            vec![java, "apt-get install -y git".into(), "apt-get install -y python3".into()],
            "harbor installs per user with uv, so it is never a root step"
        );
    }
}
