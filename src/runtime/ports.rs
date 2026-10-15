//! Host TCP port probe and auto-remap for k3d / Jenkins mappings.

use std::collections::HashSet;
use std::net::TcpListener;

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};

const CANDIDATE_OFFSET: u16 = 10_000;
const MAX_CANDIDATES: u16 = 32;

/// True when nothing appears to hold the port on IPv4 wildcard or loopback.
pub fn host_tcp_available(port: u16) -> bool {
    if port == 0 {
        return false;
    }
    {
        let _listener = match TcpListener::bind(("0.0.0.0", port)) {
            Ok(l) => l,
            Err(_) => return false,
        };
    }
    {
        let _listener = match TcpListener::bind(("127.0.0.1", port)) {
            Ok(l) => l,
            Err(_) => return false,
        };
    }
    true
}

/// Pick `preferred` if free and unused; otherwise try `preferred + 10000`, `+10001`, …
pub fn pick_free_host_port(preferred: u16, used: &HashSet<u16>) -> Result<u16> {
    if preferred != 0 && !used.contains(&preferred) && host_tcp_available(preferred) {
        return Ok(preferred);
    }

    for i in 0..MAX_CANDIDATES {
        let Some(base) = preferred.checked_add(CANDIDATE_OFFSET) else {
            break;
        };
        let Some(candidate) = base.checked_add(i) else {
            break;
        };
        if used.contains(&candidate) {
            continue;
        }
        if host_tcp_available(candidate) {
            return Ok(candidate);
        }
    }

    Err(Error::Validation(format!(
        "no free host TCP port near {preferred} (tried +{CANDIDATE_OFFSET}..+{}); free the conflicting process or set cluster.ports / jenkins.host_port in config",
        CANDIDATE_OFFSET + MAX_CANDIDATES - 1
    )))
}

/// Remap busy `cluster.ports` host values and (when Jenkins is enabled) `jenkins.host_port`.
/// Returns `(old, new)` pairs where the host port changed.
pub fn ensure_host_ports_available(config: &mut MacK3dConfig) -> Result<Vec<(u16, u16)>> {
    let mut used: HashSet<u16> = HashSet::new();
    let mut remaps = Vec::new();

    for mapping in &mut config.cluster.ports {
        let old = mapping.host;
        let new = pick_free_host_port(old, &used)?;
        used.insert(new);
        if new != old {
            mapping.host = new;
            remaps.push((old, new));
        }
    }

    if config.jenkins.enabled {
        let old = config.jenkins.host_port;
        let new = pick_free_host_port(old, &used)?;
        used.insert(new);
        if new != old {
            config.jenkins.host_port = new;
            remaps.push((old, new));
        }
    }

    Ok(remaps)
}

/// Print remaps for the user (no-op when empty).
pub fn print_port_remaps(remaps: &[(u16, u16)]) {
    for (from, to) in remaps {
        println!("Host port {from} in use → using {to}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{ClusterConfig, JenkinsConfig, MacK3dConfig, PortMapping};

    /// Anything else on the machine may take a port in the gap between
    /// releasing it and binding it again, so a single attempt is not evidence.
    const ATTEMPTS: u8 = 10;

    #[test]
    fn pick_keeps_preferred_when_free() {
        let _serial = crate::test_support::global_state();
        let used = HashSet::new();
        let mut last = (0, 0);
        for _ in 0..ATTEMPTS {
            // Ephemeral: bind 0 to get a free port, then release and pick it.
            let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
            let port = listener.local_addr().unwrap().port();
            drop(listener);
            let got = pick_free_host_port(port, &used).unwrap();
            if got == port {
                return;
            }
            last = (port, got);
        }
        panic!("every free port was taken before pick could bind it: {last:?}");
    }

    #[test]
    fn pick_remaps_when_preferred_busy() {
        let _serial = crate::test_support::global_state();
        let used = HashSet::new();
        for attempt in 1..=ATTEMPTS {
            let listener = TcpListener::bind(("0.0.0.0", 0)).unwrap();
            let busy = listener.local_addr().unwrap().port();
            let got = pick_free_host_port(busy, &used).unwrap();
            assert_ne!(got, busy);
            assert!(got >= busy.saturating_add(CANDIDATE_OFFSET) || got != busy);
            // Still holding listener — the remapped port must be free to bind.
            if host_tcp_available(got) {
                return;
            }
            assert!(attempt < ATTEMPTS, "remapped port {got} was taken every time");
        }
    }

    #[test]
    fn ensure_remaps_cluster_port_when_busy() {
        let _serial = crate::test_support::global_state();
        let listener = TcpListener::bind(("0.0.0.0", 0)).unwrap();
        let busy = listener.local_addr().unwrap().port();

        let mut config = MacK3dConfig {
            cluster: ClusterConfig {
                name: "ci-controller".into(),
                agents: 0,
                ports: vec![PortMapping {
                    host: busy,
                    container: 80,
                }],
            },
            jenkins: JenkinsConfig {
                enabled: false,
                host_port: 17070,
                ..JenkinsConfig::default()
            },
            ..MacK3dConfig::default()
        };

        let remaps = ensure_host_ports_available(&mut config).unwrap();
        assert_eq!(remaps.len(), 1);
        assert_eq!(remaps[0].0, busy);
        assert_ne!(config.cluster.ports[0].host, busy);
        assert_eq!(config.cluster.ports[0].host, remaps[0].1);
    }

    #[test]
    fn ensure_avoids_colliding_with_jenkins_port() {
        let _serial = crate::test_support::global_state();
        // Leave both preferred free; ensure they stay distinct after ensure.
        let mut config = MacK3dConfig {
            cluster: ClusterConfig {
                name: "ci-controller".into(),
                agents: 0,
                ports: vec![
                    PortMapping {
                        host: 18080,
                        container: 80,
                    },
                    PortMapping {
                        host: 18443,
                        container: 443,
                    },
                ],
            },
            jenkins: JenkinsConfig {
                enabled: true,
                host_port: 17070,
                ..JenkinsConfig::default()
            },
            ..MacK3dConfig::default()
        };

        // Skip if any of these are busy on the runner
        for p in [18080_u16, 18443, 17070] {
            if !host_tcp_available(p) {
                return;
            }
        }

        let remaps = ensure_host_ports_available(&mut config).unwrap();
        assert!(remaps.is_empty());
        let hosts: HashSet<u16> = config
            .cluster
            .ports
            .iter()
            .map(|p| p.host)
            .chain(std::iter::once(config.jenkins.host_port))
            .collect();
        assert_eq!(hosts.len(), 3);
    }
}
