use agent_skills::{DD_APM_SUB_SKILLS, DD_LLMO_SUB_SKILLS};

fn frontmatter_name(body: &str) -> &str {
    body.lines()
        .find_map(|line| line.strip_prefix("name:"))
        .map(str::trim)
        .unwrap_or("")
}

fn body<'a>(skills: &'a [(&str, &str)], key: &str) -> &'a str {
    skills
        .iter()
        .find(|(candidate, _)| *candidate == key)
        .unwrap_or_else(|| panic!("missing embedded skill {key}"))
        .1
}

#[test]
fn similar_observability_skills_keep_their_own_names() {
    let names = [
        "agent-observability-eval-bootstrap",
        "agent-observability-eval-pipeline",
        "agent-observability-experiment-analyzer",
        "agent-observability-experiment-bootstrap",
        "agent-observability-session-classify",
        "agent-observability-trace-rca",
        "agent-observability-replay-trace",
    ];

    for name in names {
        let skill = body(DD_LLMO_SUB_SKILLS, &format!("{name}/SKILL.md"));
        assert_eq!(frontmatter_name(skill), name);
    }
}

#[test]
fn kubernetes_and_linux_copies_stay_distinct() {
    let pairs = [
        (
            "agent-install/SKILL.md",
            "Install the Datadog Agent on Kubernetes",
            "Install Datadog Agent on Linux",
        ),
        (
            "enable-ssi/SKILL.md",
            "Enable APM on Kubernetes",
            "Configure SSI and Unified Service Tags on Linux",
        ),
        (
            "verify-ssi/SKILL.md",
            "Verify APM SSI on Kubernetes",
            "Verify APM SSI on Linux",
        ),
        (
            "troubleshoot-ssi/SKILL.md",
            "Troubleshoot APM SSI on Kubernetes",
            "Troubleshoot APM SSI on Linux",
        ),
        (
            "onboarding-summary/SKILL.md",
            "tags: datadog,apm,kubernetes,ssi,summary,verification",
            "APM Onboarding Summary — Linux Host",
        ),
    ];

    for (leaf, kubernetes_marker, linux_marker) in pairs {
        let kubernetes = body(DD_APM_SUB_SKILLS, &format!("k8s-ssi/{leaf}"));
        let linux = body(DD_APM_SUB_SKILLS, &format!("linux-ssi/{leaf}"));
        assert_eq!(frontmatter_name(kubernetes), frontmatter_name(linux));
        assert_ne!(kubernetes, linux);
        assert!(
            kubernetes.contains(kubernetes_marker),
            "k8s-ssi/{leaf} lost its Kubernetes marker"
        );
        assert!(
            linux.contains(linux_marker),
            "linux-ssi/{leaf} lost its Linux marker"
        );
        assert!(
            !kubernetes.contains(linux_marker),
            "k8s-ssi/{leaf} contains the Linux copy"
        );
        assert!(
            !linux.contains(kubernetes_marker),
            "linux-ssi/{leaf} contains the Kubernetes copy"
        );
    }
}
